import asyncio
import base64
import json
import os
import tempfile

import carb
import carb.events
from carb.eventdispatcher import get_eventdispatcher

import omni.ext
import omni.kit.app
import omni.kit.asset_converter as asset_converter
import omni.kit.livestream.messaging as messaging


class Extension(omni.ext.IExt):
    """Accepts USD->GLB conversion requests over Kit messaging."""

    def on_startup(self, ext_id):
        self._subscriptions = []
        self._converter = asset_converter.get_instance()
        self._ws = None
        self._ws_task = None

        self._outgoing_events = [
            "convertUsdToGlbAccepted",
            "convertUsdToGlbProgress",
            "convertUsdToGlbResult",
        ]
        for event_name in self._outgoing_events:
            messaging.register_event_type_to_send(event_name)
            omni.kit.app.register_event_alias(carb.events.type_from_string(event_name), event_name)

        incoming_name = "convertUsdToGlbRequest"
        omni.kit.app.register_event_alias(carb.events.type_from_string(incoming_name), incoming_name)
        self._subscriptions.append(
            get_eventdispatcher().observe_event(
                observer_name="UsdToGlbConverter:convertUsdToGlbRequest",
                event_name=incoming_name,
                on_event=self._on_convert_request,
            )
        )

        self._ws_task = asyncio.ensure_future(self._ws_client_loop())

    def _schedule_task(self, coro):
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(coro)
        except RuntimeError:
            # If loop is not running (e.g. shutdown race), close coroutine to avoid warnings.
            try:
                coro.close()
            except Exception:
                pass

    def _on_convert_request(self, event: carb.events.IEvent):
        payload = dict(event.payload) if event.payload else {}
        self._handle_convert_payload(payload)

    def _handle_convert_payload(self, payload: dict):
        request_id = payload.get("requestId")
        usd_url = payload.get("usdUrl")
        glb_output_url = payload.get("glbOutputUrl")
        usd_base64 = payload.get("usdBase64")
        usd_file_name = payload.get("usdFileName")
        glb_file_name = payload.get("glbFileName")
        source_usd_file_id = payload.get("sourceUsdFileId")
        equipment_group_id = payload.get("equipmentGroupId")

        if (not usd_url and not usd_base64) or (not glb_output_url and not glb_file_name):
            error_message = 'Missing required fields: ("usdUrl" or "usdBase64"), ("glbOutputUrl" or "glbFileName")'
            get_eventdispatcher().dispatch_event(
                "convertUsdToGlbResult",
                payload={
                    "requestId": request_id,
                    "status": "error",
                    "message": error_message,
                },
            )
            self._schedule_task(
                self._send_ws_message(
                    {
                        "type": "convertUsdToGlbResult",
                        "requestId": request_id,
                        "status": "error",
                        "message": error_message,
                    }
                )
            )
            return

        carb.log_info(f"[usd_to_glb_converter] accepted requestId={request_id}")
        accepted_payload = {
            "requestId": request_id,
            "status": "accepted",
            "usdUrl": usd_url,
            "glbOutputUrl": glb_output_url,
            "sourceUsdFileId": source_usd_file_id,
            "equipmentGroupId": equipment_group_id,
        }
        get_eventdispatcher().dispatch_event("convertUsdToGlbAccepted", payload=accepted_payload)
        self._schedule_task(self._send_ws_message({"type": "convertUsdToGlbAccepted", **accepted_payload}))
        self._schedule_task(
            self._run_conversion(
                request_id=request_id,
                usd_url=usd_url,
                glb_output_url=glb_output_url,
                usd_base64=usd_base64,
                usd_file_name=usd_file_name,
                glb_file_name=glb_file_name,
                source_usd_file_id=source_usd_file_id,
                equipment_group_id=equipment_group_id,
            )
        )

    async def _run_conversion(
        self,
        request_id,
        usd_url,
        glb_output_url,
        usd_base64,
        usd_file_name,
        glb_file_name,
        source_usd_file_id,
        equipment_group_id,
    ):
        temp_usd_path = None
        temp_glb_path = None
        try:
            if usd_base64:
                safe_usd_name = usd_file_name or f"{request_id}.usd"
                safe_glb_name = glb_file_name or f"{request_id}.glb"
                temp_usd_path = os.path.join(tempfile.gettempdir(), f"{request_id}_{safe_usd_name}")
                temp_glb_path = os.path.join(tempfile.gettempdir(), f"{request_id}_{safe_glb_name}")

                with open(temp_usd_path, "wb") as f:
                    f.write(base64.b64decode(usd_base64))

                usd_url = temp_usd_path
                glb_output_url = temp_glb_path

            converter_context = asset_converter.AssetConverterContext()
            if hasattr(converter_context, "keep_all_materials"):
                converter_context.keep_all_materials = True

            def on_progress(*args):
                get_eventdispatcher().dispatch_event(
                    "convertUsdToGlbProgress",
                    payload=self._build_progress_payload(request_id, usd_url, glb_output_url, args),
                )
                progress_payload = self._build_progress_payload(request_id, usd_url, glb_output_url, args)
                progress_payload["sourceUsdFileId"] = source_usd_file_id
                progress_payload["equipmentGroupId"] = equipment_group_id
                self._schedule_task(
                    self._send_ws_message({"type": "convertUsdToGlbProgress", **progress_payload})
                )

            task = self._converter.create_converter_task(
                usd_url,
                glb_output_url,
                on_progress,
                converter_context,
            )
            success = await task.wait_until_finished()
            if not success:
                raise RuntimeError("asset converter reported conversion failure")

            carb.log_info(f"[usd_to_glb_converter] completed requestId={request_id}")
            result_payload = {
                "requestId": request_id,
                "status": "success",
                "usdUrl": usd_url,
                "glbOutputUrl": glb_output_url,
                "sourceUsdFileId": source_usd_file_id,
                "equipmentGroupId": equipment_group_id,
            }

            if temp_glb_path and os.path.exists(temp_glb_path):
                with open(temp_glb_path, "rb") as f:
                    result_payload["glbBase64"] = base64.b64encode(f.read()).decode("ascii")
                result_payload["glbFileName"] = os.path.basename(temp_glb_path)

            get_eventdispatcher().dispatch_event("convertUsdToGlbResult", payload=result_payload)
            self._schedule_task(self._send_ws_message({"type": "convertUsdToGlbResult", **result_payload}))
        except Exception as exc:
            carb.log_error(f"[usd_to_glb_converter] failed requestId={request_id}: {exc}")
            error_payload = {
                "requestId": request_id,
                "status": "error",
                "usdUrl": usd_url,
                "glbOutputUrl": glb_output_url,
                "message": str(exc),
                "sourceUsdFileId": source_usd_file_id,
                "equipmentGroupId": equipment_group_id,
            }
            get_eventdispatcher().dispatch_event("convertUsdToGlbResult", payload=error_payload)
            self._schedule_task(self._send_ws_message({"type": "convertUsdToGlbResult", **error_payload}))
        finally:
            for temp_path in (temp_usd_path, temp_glb_path):
                if temp_path and os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass

    def _build_progress_payload(self, request_id, usd_url, glb_output_url, args):
        payload = {
            "requestId": request_id,
            "status": "progress",
            "usdUrl": usd_url,
            "glbOutputUrl": glb_output_url,
        }

        # asset converter callback signature can vary by Kit version.
        # We parse common numeric patterns and still expose raw arguments.
        numeric = [x for x in args if isinstance(x, (int, float))]
        if len(numeric) >= 2 and numeric[1] not in (0, 0.0):
            current = float(numeric[0])
            total = float(numeric[1])
            payload["current"] = current
            payload["total"] = total
            payload["progress"] = max(0.0, min(100.0, (current / total) * 100.0))
        elif len(numeric) >= 1:
            one = float(numeric[0])
            payload["progress"] = max(0.0, min(100.0, one * 100.0 if one <= 1.0 else one))

        payload["rawArgs"] = [str(x) for x in args]
        return payload

    async def _ws_client_loop(self):
        ws_url = os.getenv("KIT_CONVERTER_WS_URL", "ws://127.0.0.1:8000/ws/kit-converter")
        reconnect_delay_sec = 2.0
        max_ws_message_mb = int(os.getenv("KIT_CONVERTER_WS_MAX_MESSAGE_MB", "256"))
        max_ws_message_bytes = max_ws_message_mb * 1024 * 1024

        try:
            import websockets
        except Exception as exc:
            carb.log_warn(f"[usd_to_glb_converter] websockets module unavailable: {exc}")
            return

        while True:
            try:
                async with websockets.connect(ws_url, max_size=max_ws_message_bytes) as ws:
                    self._ws = ws
                    await ws.send(json.dumps({"type": "register", "role": "kit-converter"}))
                    carb.log_info(f"[usd_to_glb_converter] connected websocket: {ws_url}")

                    async for raw_message in ws:
                        try:
                            msg = json.loads(raw_message)
                        except Exception:
                            continue

                        if msg.get("type") == "convertUsdToGlbRequest":
                            self._handle_convert_payload(msg)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                carb.log_warn(f"[usd_to_glb_converter] websocket reconnecting: {exc}")
            finally:
                self._ws = None

            await asyncio.sleep(reconnect_delay_sec)

    async def _send_ws_message(self, message: dict):
        if not self._ws:
            return
        try:
            await self._ws.send(json.dumps(message))
        except Exception as exc:
            carb.log_warn(f"[usd_to_glb_converter] failed to send websocket message: {exc}")

    def on_shutdown(self):
        if hasattr(self, "_subscriptions") and self._subscriptions:
            self._subscriptions.clear()
        if self._ws_task:
            self._ws_task.cancel()


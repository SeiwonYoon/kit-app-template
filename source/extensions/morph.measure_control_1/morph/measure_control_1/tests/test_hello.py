import morph.measure_control
import omni.kit.test


class Test(omni.kit.test.AsyncTestCaseFailOnLogError):
    async def test_public_entrypoint_exists(self):
        self.assertTrue(hasattr(morph.measure_control, "get_service"))

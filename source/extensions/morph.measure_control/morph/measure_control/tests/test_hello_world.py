import morph.measure_control
import omni.kit.test


class Test(omni.kit.test.AsyncTestCase):
    async def test_module_import(self):
        self.assertIsNotNone(morph.measure_control)

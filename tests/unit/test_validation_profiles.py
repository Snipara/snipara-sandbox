from rlm.validation.models import ValidationRequest
from rlm.validation.profiles import runtime_commands


def test_pnpm_is_explicit_and_profiles_require_real_runtimes():
    request = ValidationRequest(
        workspace="/tmp/project", argv=["pnpm", "test"], image="prepared:local", profile="mixed"
    )
    assert runtime_commands(request) == ["python", "node", "npm", "pnpm"]
    assert runtime_commands(
        request.model_copy(update={"profile": "node", "argv": ["node", "test.js"]})
    ) == ["node", "npm"]

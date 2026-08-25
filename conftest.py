"""Make the `src/` layout importable in tests without an editable install."""

import os
import pathlib
import sys

_SRC = pathlib.Path(__file__).parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# The test suite has no real Anthropic API key, so run the LLM in demo mode by
# default. bootstrap.build_container now fails closed when neither a key nor
# this flag is set (see step 11); enabling it here keeps container-building
# tests green without a per-call-site edit. setdefault respects an explicit
# value from the environment (e.g. a developer running against a real key).
# The dedicated fail-closed test overrides this back off via an explicit
# Settings(llm_demo_mode=False).
os.environ.setdefault("SKYLIZE_LLM_DEMO_MODE", "true")

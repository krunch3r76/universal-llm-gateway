"""Unit tests for closeout route-probe observe evaluator and command parse (AC4)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from implement_admission.closeout_route_probe_gate import evaluate_closeout_route_probes
from implement_admission.route_probe_command_parse import parse_route_probe_command
from universal_event_bus.events.validation import is_valid_event_signal

_TEST_MODULE = "libs/cdp_ask/test_active_work.py"


def test_signal_names_validate() -> None:
    assert is_valid_event_signal("frontier.closeout.routeprobe.evaluated")
    assert is_valid_event_signal("frontier.closeout.routeprobe.check.failed")


def test_trigger_arms_and_attribution() -> None:
    reach_calls: list[tuple[str, str]] = []

    def reach(slug: str, path: str) -> bool:
        reach_calls.append((slug, path))
        return path.endswith("libs/shared_x/leaf.py")

    v = evaluate_closeout_route_probes(
        {
            "propagation": [{"service": "cdp_ask", "code_ref": "HEAD"}],
        },
        reach=lambda s, p: False,
    )
    assert v.would_require is True
    assert v.required[0]["named_by"] == ["row:untagged"]

    v = evaluate_closeout_route_probes(
        {
            "propagation": [
                {
                    "service": "cdp_ask",
                    "reason": "derived:consumers; import_path:verified",
                }
            ],
        },
        reach=lambda s, p: False,
    )
    assert v.arms == ["named"]
    assert v.required[0]["named_by"] == ["row:derived:consumers"]

    v = evaluate_closeout_route_probes(
        {
            "propagation": [
                {
                    "service": "cdp_ask",
                    "reason": "derived:ownership; import_path:verified",
                }
            ],
        },
        reach=lambda s, p: False,
    )
    assert v.required[0]["named_by"] == ["row:derived:ownership"]

    v = evaluate_closeout_route_probes(
        {
            "propagation": [
                {
                    "service": "cdp_ask",
                    "reason": "derived:path_prefix; import_path:not_probed",
                }
            ],
        },
        reach=lambda s, p: False,
    )
    assert v.would_require is False
    assert v.path_prefix_only == ["cdp_ask"]

    v = evaluate_closeout_route_probes(
        {
            "propagation_residue": [
                'sync_restart: cdp_ask — manage(action="sync_restart", service="cdp_ask")'
            ],
        },
        reach=lambda s, p: False,
    )
    assert v.required[0]["named_by"] == ["residue:untagged"]

    v = evaluate_closeout_route_probes(
        {
            "propagation_residue": [
                'sync_restart: cdp_ask — manage(action="sync_restart", service="cdp_ask"); '
                "derived:path_prefix; import_path:not_probed"
            ],
        },
        reach=lambda s, p: False,
    )
    assert v.would_require is False

    v = evaluate_closeout_route_probes(
        {"files_modified": ["libs/shared_x/leaf.py"]},
        reach=reach,
    )
    assert v.arms == ["reached"]
    assert v.required[0]["reached_via"] == "libs/shared_x/leaf.py"

    v = evaluate_closeout_route_probes(
        {
            "propagation": [{"service": "cdp_ask"}],
            "files_modified": ["libs/shared_x/leaf.py"],
        },
        reach=reach,
    )
    assert v.arms == ["named", "reached"]

    v = evaluate_closeout_route_probes(
        {"files_modified": ["libs/none/none.py"]},
        reach=lambda s, p: False,
    )
    assert v.would_require is False

    v = evaluate_closeout_route_probes(
        {"propagation": [{"service": "rag"}]},
        reach=lambda s, p: False,
    )
    assert v.unmapped == ["rag"]
    assert v.would_require is False

    test_mod_calls: list[tuple[str, str]] = []

    def reach_rec(slug: str, path: str) -> bool:
        test_mod_calls.append((slug, path))
        return True

    v = evaluate_closeout_route_probes(
        {"files_modified": ["libs/cdp_ask/test_x.py"]},
        reach=reach_rec,
    )
    assert v.would_require is False
    assert test_mod_calls == []

    paths = ["libs/a/first.py", "libs/b/second.py"]

    def reach_err(slug: str, path: str) -> bool:
        if path == paths[0]:
            raise OSError("nope")
        return path == paths[1]

    v = evaluate_closeout_route_probes(
        {"files_modified": paths},
        reach=reach_err,
    )
    assert v.reach_errors == [
        {"slug": "cdp_ask", "path": paths[0], "error_type": "OSError"}
    ]
    assert v.required[0]["reached_via"] == paths[1]

    def reach_both_err(slug: str, path: str) -> bool:
        raise OSError("nope")

    v = evaluate_closeout_route_probes(
        {"propagation": [{"service": "cdp_ask"}], "files_modified": paths},
        reach=reach_both_err,
    )
    assert v.would_require is True
    assert len(v.required) == 1
    assert v.required[0]["slug"] == "cdp_ask"
    assert v.required[0]["reached"] is None
    assert len(v.reach_errors) == 2


def test_path_prefix_only_excludes_counting_slug() -> None:
    v = evaluate_closeout_route_probes(
        {
            "propagation": [
                {"service": "cdp_ask"},
                {
                    "service": "cdp_ask",
                    "reason": "derived:path_prefix; import_path:not_probed",
                },
            ],
        },
        reach=lambda s, p: False,
    )
    assert v.would_require is True
    assert v.required[0]["named_by"] == ["row:untagged"]
    assert v.path_prefix_only == []


def test_unmapped_path_prefix_excluded() -> None:
    v = evaluate_closeout_route_probes(
        {
            "propagation": [
                {
                    "service": "rag",
                    "reason": "derived:path_prefix; import_path:not_probed",
                },
            ],
        },
        reach=lambda s, p: False,
    )
    assert v.path_prefix_only == []
    assert v.unmapped == []
    assert v.would_require is False


def test_evidence_reason_codes() -> None:
    base = {"propagation": [{"service": "cdp_ask"}]}

    v = evaluate_closeout_route_probes(base, reach=lambda s, p: False)
    assert v.missing[0]["reason_code"] == "no_candidate_row"

    v = evaluate_closeout_route_probes(
        {
            **base,
            "verification": [{"command": f"pytest {_TEST_MODULE} -q"}],
        },
        reach=lambda s, p: False,
    )
    m = v.missing[0]
    assert m["reason_code"] == "not_observed"
    assert m["candidate_register"] is None

    v = evaluate_closeout_route_probes(
        {
            **base,
            "verification": [
                {
                    "command": f"pytest {_TEST_MODULE} -q",
                    "exit_code_register": "unattributed",
                    "exit_code": None,
                    "wrapper_exit_code": 0,
                }
            ],
        },
        reach=lambda s, p: False,
    )
    assert v.missing[0]["reason_code"] == "not_observed"
    assert v.missing[0]["candidate_register"] == "unattributed"

    v = evaluate_closeout_route_probes(
        {
            **base,
            "verification": [
                {
                    "command": f"pytest {_TEST_MODULE} -q",
                    "exit_code_register": "observed",
                    "exit_code": 1,
                    "invocation_id": "x",
                }
            ],
        },
        reach=lambda s, p: False,
    )
    assert v.missing[0]["reason_code"] == "nonzero_exit"

    v = evaluate_closeout_route_probes(
        {
            **base,
            "verification": [
                {
                    "command": f"pytest {_TEST_MODULE} -q",
                    "exit_code_register": "observed",
                    "exit_code": True,
                    "invocation_id": "x",
                }
            ],
        },
        reach=lambda s, p: False,
    )
    assert v.missing[0]["reason_code"] == "nonzero_exit"

    v = evaluate_closeout_route_probes(
        {
            **base,
            "verification": [
                {
                    "command": f"pytest {_TEST_MODULE} -q",
                    "exit_code_register": "observed",
                    "exit_code": False,
                    "invocation_id": "x",
                }
            ],
        },
        reach=lambda s, p: False,
    )
    assert v.missing[0]["reason_code"] == "nonzero_exit"

    v = evaluate_closeout_route_probes(
        {
            **base,
            "verification": [
                {
                    "command": f"pytest {_TEST_MODULE} -q",
                    "exit_code_register": "observed",
                    "exit_code": 0,
                    "invocation_id": "",
                }
            ],
        },
        reach=lambda s, p: False,
    )
    assert v.missing[0]["reason_code"] == "no_invocation_id"

    v = evaluate_closeout_route_probes(
        {
            **base,
            "verification": [
                {
                    "command": f"pytest {_TEST_MODULE} -q > out.txt",
                    "exit_code_register": "observed",
                    "exit_code": 0,
                    "invocation_id": "id1",
                }
            ],
        },
        reach=lambda s, p: False,
    )
    assert v.missing[0]["reason_code"] == "command_shape"
    assert v.missing[0]["parse_refusal"] == "redirect"


@pytest.mark.parametrize(
    "command",
    [
        f"pytest {_TEST_MODULE} -q",
        f"pytest {_TEST_MODULE} -q 2>&1",
        f"pytest {_TEST_MODULE} -q --color=no",
        f"echo x; pytest {_TEST_MODULE} -q",
        f"cd universal-llm-gateway && pytest {_TEST_MODULE} -q",
        "cd libs && pytest cdp_ask/test_active_work.py -q",
        f"PYTHONPATH=libs python -m pytest {_TEST_MODULE}",
        f"env PYTHONPATH=libs pytest {_TEST_MODULE} -q",
        f"pytest -p no:cacheprovider {_TEST_MODULE}",
        f"pytest libs/cdp_ask/test_active_work.py\\\n -q",
    ],
)
def test_parse_accepts(command: str) -> None:
    ok, refusal = parse_route_probe_command(command, test_module=_TEST_MODULE)
    assert (ok, refusal) == (True, None)


@pytest.mark.parametrize(
    "command,code",
    [
        (f"pytest {_TEST_MODULE} -q 2>&1 | tail -5", "pipe"),
        (f"pytest {_TEST_MODULE} -q | tail -5", "pipe"),
        (f"pytest {_TEST_MODULE} -q > out.txt 2>&1", "redirect"),
        (
            f"pytest {_TEST_MODULE}; pytest libs/x/test_y.py",
            "module_path_absent",
        ),
        ("pytest libs/cdp_ask -q", "module_path_absent"),
        ("pytest other/cdp_ask/test_active_work.py -q", "module_path_absent"),
        (f"pytest {_TEST_MODULE} -kunified", "filter_or_selection"),
        (f"pytest {_TEST_MODULE} -k unified_chat", "filter_or_selection"),
        (f"pytest {_TEST_MODULE} --deselect=x", "filter_or_selection"),
        (f'pytest {_TEST_MODULE} -m "not slow"', "filter_or_selection"),
        (f"pytest {_TEST_MODULE} -o addopts=-kx", "filter_or_selection"),
        (f"pytest {_TEST_MODULE}::test_active_work_endpoint_idle", "node_id"),
        ("env PYTEST_ADDOPTS=-kx pytest " + _TEST_MODULE, "env_prefix"),
        (f"env -i pytest {_TEST_MODULE}", "env_prefix"),
        (f"env -u X pytest {_TEST_MODULE}", "env_prefix"),
        (f"timeout 600 pytest {_TEST_MODULE}", "executable"),
        (f"pytest {_TEST_MODULE} -q; echo \"PYTEST_EXIT:$?\"", "last_segment_not_pytest"),
        (f"pytest {_TEST_MODULE} -q || true", "last_segment_not_pytest"),
        (f"pytest {_TEST_MODULE} --cov=x", "unknown_flag"),
        (f"pytest {_TEST_MODULE} -q &", "unknown_flag"),
        ('pytest "unterminated', "unparseable"),
        ("pytest `whoami`", "substitution"),
        ("PYTHONPATH=libs/cdp_ask/test_active_work.py", "last_segment_not_pytest"),
    ],
)
def test_parse_refusals(command: str, code: str) -> None:
    ok, refusal = parse_route_probe_command(command, test_module=_TEST_MODULE)
    assert ok is False
    assert refusal == code


def test_totality() -> None:
    v = evaluate_closeout_route_probes(
        {
            "propagation": "x",
            "verification": [1, None, {"command": 5}],
            "files_modified": [3, "libs/a.py"],
        }
    )
    assert v.input_defects == ["files_modified[]", "propagation", "verification[]"]

    v = evaluate_closeout_route_probes([])
    assert v.input_defects == ["closeout"]
    assert v.would_require is False


def _write_reach_tree(root: Path) -> None:
    (root / "libs" / "cdp_ask").mkdir(parents=True)
    (root / "libs" / "cdp_ask" / "__init__.py").write_text("", encoding="utf-8")
    (root / "libs" / "cdp_ask" / "test_active_work.py").write_text(
        "from cdp_ask.helper_mod import value\n",
        encoding="utf-8",
    )
    (root / "libs" / "cdp_ask" / "helper_mod.py").write_text(
        "from shared_x.leaf import thing\nvalue = 1\n",
        encoding="utf-8",
    )
    (root / "libs" / "shared_x").mkdir(parents=True)
    (root / "libs" / "shared_x" / "__init__.py").write_text("", encoding="utf-8")
    (root / "libs" / "shared_x" / "leaf.py").write_text("thing = 1\n", encoding="utf-8")
    (root / "libs" / "shared_x" / "unreached.py").write_text("x = 1\n", encoding="utf-8")


def test_default_reach_on_tmp_tree(tmp_path: Path) -> None:
    _write_reach_tree(tmp_path)
    v = evaluate_closeout_route_probes(
        {"files_modified": ["libs/shared_x/leaf.py"]},
        root=tmp_path,
    )
    assert v.arms == ["reached"]
    assert v.required[0]["reached_via"] == "libs/shared_x/leaf.py"
    assert v.reach_errors == []

    v = evaluate_closeout_route_probes(
        {"files_modified": ["libs/shared_x/unreached.py"]},
        root=tmp_path,
    )
    assert v.would_require is False
    assert v.required == []


def test_reach_overrun_is_not_reached(tmp_path: Path) -> None:
    _write_reach_tree(tmp_path)
    v = evaluate_closeout_route_probes(
        {"files_modified": ["libs/shared_x/leaf.py"]},
        root=tmp_path,
        reach_budget_s=0.0,
    )
    assert v.would_require is False
    assert v.required == []
    assert v.reach_errors == [
        {
            "slug": "cdp_ask",
            "path": "libs/shared_x/leaf.py",
            "error_type": "RouteProbeReachIncomplete",
        }
    ]

    v = evaluate_closeout_route_probes(
        {
            "propagation": [{"service": "cdp_ask"}],
            "files_modified": ["libs/shared_x/leaf.py"],
        },
        root=tmp_path,
        reach_budget_s=0.0,
    )
    assert v.would_require is True
    assert v.required[0]["named"] is True
    assert v.required[0]["reached"] is None
    assert v.arms == ["named"]
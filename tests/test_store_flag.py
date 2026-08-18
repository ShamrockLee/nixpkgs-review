from __future__ import annotations

from pathlib import Path

from nixpkgs_review.allow import AllowedFeatures
from nixpkgs_review.cli import parse_args
from nixpkgs_review.nix import (
    BuildConfig,
    _eval_store_flags,
    _nix_common_flags,
    _store_flags,
)
from nixpkgs_review.review import build_config_from_args


def test_store_flag_defaults_to_none() -> None:
    args = parse_args("nixpkgs-review", ["rev", "HEAD"])
    assert args.store is None
    assert args.eval_store is None


def test_store_flag_parsed_for_pr_rev_wip() -> None:
    for subcommand, extra in (
        ("pr", ["1"]),
        ("rev", ["HEAD"]),
        ("wip", []),
    ):
        args = parse_args(
            "nixpkgs-review", [subcommand, *extra, "--store", "local?root=/tmp/x"]
        )
        assert args.store == "local?root=/tmp/x"


def test_eval_store_flag_parsed_for_pr_rev_wip() -> None:
    for subcommand, extra in (
        ("pr", ["1"]),
        ("rev", ["HEAD"]),
        ("wip", []),
    ):
        args = parse_args(
            "nixpkgs-review", [subcommand, *extra, "--eval-store", "auto"]
        )
        assert args.eval_store == "auto"


def test_store_flags_helper() -> None:
    assert _store_flags(None) == []
    assert _store_flags("local?root=/tmp/x") == ["--store", "local?root=/tmp/x"]


def test_eval_store_flags_helper() -> None:
    assert _eval_store_flags(None) == []
    assert _eval_store_flags("auto") == ["--eval-store", "auto"]


def test_nix_common_flags_includes_store_when_set() -> None:
    allow = AllowedFeatures([])
    flags = _nix_common_flags(allow, "", "local?root=/tmp/x")
    assert flags[-2:] == ["--store", "local?root=/tmp/x"]


def test_nix_common_flags_includes_eval_store_when_set() -> None:
    allow = AllowedFeatures([])
    flags = _nix_common_flags(allow, "", "local?root=/tmp/x", "auto")
    assert flags[-4:] == ["--store", "local?root=/tmp/x", "--eval-store", "auto"]


def test_nix_common_flags_omits_store_when_unset() -> None:
    allow = AllowedFeatures([])
    flags = _nix_common_flags(allow, "")
    assert "--store" not in flags
    assert "--eval-store" not in flags


def test_build_config_from_args_carries_store() -> None:
    args = parse_args(
        "nixpkgs-review",
        ["rev", "HEAD", "--store", "local?root=/tmp/x", "--eval-store", "auto"],
    )
    build_config: BuildConfig = build_config_from_args(
        args, AllowedFeatures([]), nix_path="", nixpkgs_config=Path("/dev/null")
    )
    assert build_config.store == "local?root=/tmp/x"
    assert build_config.eval_store == "auto"

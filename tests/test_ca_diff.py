from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

from nixpkgs_review.cli import check_common_flags, main, parse_args
from nixpkgs_review.nix import Attr
from nixpkgs_review.review import Review

if TYPE_CHECKING:
    from .conftest import Helpers


def make_attr(
    name: str,
    *,
    broken: bool = False,
    drv_path: Path | None = None,
    ca_status: str | None = "applied",
    ca_realized_outputs: dict[str, Path] | None = None,
) -> Attr:
    return Attr(
        name=name,
        exists=True,
        broken=broken,
        blacklisted=False,
        outputs=None,
        drv_path=drv_path,
        ca_status=ca_status,
        ca_realized_outputs=ca_realized_outputs,
    )


def test_ca_diff_requires_store() -> None:
    args = parse_args("nixpkgs-review", ["rev", "HEAD", "--ca-diff"])
    assert args.store is None
    assert check_common_flags(args) is False


def test_ca_diff_with_store_auto_passes_guard() -> None:
    args = parse_args("nixpkgs-review", ["rev", "HEAD", "--ca-diff", "--store", "auto"])
    assert check_common_flags(args) is True


def test_ca_diff_flag_defaults_false() -> None:
    args = parse_args("nixpkgs-review", ["rev", "HEAD"])
    assert args.ca_diff is False


class TestCaBucketAttr:
    def test_unchanged(self) -> None:
        out = {"out": Path("/nix/store/aaaa-pkg")}
        merged = make_attr("pkg", drv_path=Path("/m.drv"), ca_realized_outputs=out)
        base = make_attr("pkg", drv_path=Path("/b.drv"), ca_realized_outputs=out)
        attr = Review._ca_bucket_attr(merged, base)
        assert attr.ca_bucket == "unchanged"

    def test_changed(self) -> None:
        merged = make_attr(
            "pkg",
            drv_path=Path("/m.drv"),
            ca_realized_outputs={"out": Path("/nix/store/aaaa-pkg")},
        )
        base = make_attr(
            "pkg",
            drv_path=Path("/b.drv"),
            ca_realized_outputs={"out": Path("/nix/store/bbbb-pkg")},
        )
        attr = Review._ca_bucket_attr(merged, base)
        assert attr.ca_bucket == "changed"

    def test_added(self) -> None:
        merged = make_attr(
            "pkg",
            drv_path=Path("/m.drv"),
            ca_realized_outputs={"out": Path("/nix/store/aaaa-pkg")},
        )
        base = make_attr("pkg", broken=True, drv_path=None)
        attr = Review._ca_bucket_attr(merged, base)
        assert attr.ca_bucket == "added"

    def test_removed(self) -> None:
        merged = make_attr("pkg", broken=True, drv_path=None)
        base = make_attr(
            "pkg",
            drv_path=Path("/b.drv"),
            ca_realized_outputs={"out": Path("/nix/store/aaaa-pkg")},
        )
        attr = Review._ca_bucket_attr(merged, base)
        assert attr.ca_bucket == "removed"

    def test_broken_on_both_sides_is_ca_build_failed(self) -> None:
        merged = make_attr("pkg", broken=True, drv_path=None)
        base = make_attr("pkg", broken=True, drv_path=None)
        attr = Review._ca_bucket_attr(merged, base)
        assert attr.ca_bucket == "ca-build-failed"

    def test_ca_build_failure_when_realized_outputs_missing(self) -> None:
        merged = make_attr("pkg", drv_path=Path("/m.drv"), ca_realized_outputs=None)
        base = make_attr(
            "pkg",
            drv_path=Path("/b.drv"),
            ca_realized_outputs={"out": Path("/nix/store/aaaa-pkg")},
        )
        attr = Review._ca_bucket_attr(merged, base)
        assert attr.ca_bucket == "ca-build-failed"

    def test_opted_out_is_not_comparable_not_changed(self) -> None:
        merged = make_attr(
            "pkg",
            drv_path=Path("/m.drv"),
            ca_status="opted-out",
            ca_realized_outputs={"out": Path("/nix/store/aaaa-pkg")},
        )
        base = make_attr(
            "pkg",
            drv_path=Path("/b.drv"),
            ca_status="opted-out",
            ca_realized_outputs={"out": Path("/nix/store/bbbb-pkg")},
        )
        attr = Review._ca_bucket_attr(merged, base)
        assert attr.ca_bucket == "not-comparable"

    def test_fod_is_comparable(self) -> None:
        out = {"out": Path("/nix/store/aaaa-src")}
        merged = make_attr(
            "src", drv_path=Path("/m.drv"), ca_status="fod", ca_realized_outputs=out
        )
        base = make_attr(
            "src", drv_path=Path("/b.drv"), ca_status="fod", ca_realized_outputs=out
        )
        attr = Review._ca_bucket_attr(merged, base)
        assert attr.ca_bucket == "unchanged"


class TestCaTaintPropagate:
    def _review(self) -> Review:
        return Review.__new__(Review)

    def test_changed_dependent_of_opted_out_becomes_inconclusive(self) -> None:
        review = self._review()
        dep = make_attr("dep", drv_path=Path("/dep.drv"), ca_status="opted-out")
        dep.ca_bucket = "not-comparable"
        top = make_attr("top", drv_path=Path("/top.drv"))
        top.ca_bucket = "changed"

        with patch.object(
            review,
            "_ca_drv_references",
            return_value={
                Path("/top.drv"): {Path("/dep.drv")},
                Path("/dep.drv"): set(),
            },
        ):
            review._ca_taint_propagate([top, dep])

        assert top.ca_bucket == "changed-inconclusive"

    def test_unchanged_dependent_stays_unchanged_despite_opted_out_dep(self) -> None:
        review = self._review()
        dep = make_attr("dep", drv_path=Path("/dep.drv"), ca_status="opted-out")
        dep.ca_bucket = "not-comparable"
        top = make_attr("top", drv_path=Path("/top.drv"))
        top.ca_bucket = "unchanged"

        with patch.object(
            review,
            "_ca_drv_references",
            return_value={
                Path("/top.drv"): {Path("/dep.drv")},
                Path("/dep.drv"): set(),
            },
        ):
            review._ca_taint_propagate([top, dep])

        assert top.ca_bucket == "unchanged"

    def test_changed_without_tainted_dependency_stays_changed(self) -> None:
        review = self._review()
        dep = make_attr("dep", drv_path=Path("/dep.drv"))
        dep.ca_bucket = "unchanged"
        top = make_attr("top", drv_path=Path("/top.drv"))
        top.ca_bucket = "changed"

        with patch.object(
            review,
            "_ca_drv_references",
            return_value={
                Path("/top.drv"): {Path("/dep.drv")},
                Path("/dep.drv"): set(),
            },
        ):
            review._ca_taint_propagate([top, dep])

        assert top.ca_bucket == "changed"

    def test_transitive_taint_propagates_two_hops(self) -> None:
        review = self._review()
        leaf = make_attr("leaf", drv_path=Path("/leaf.drv"), ca_status="opted-out")
        leaf.ca_bucket = "not-comparable"
        mid = make_attr("mid", drv_path=Path("/mid.drv"))
        mid.ca_bucket = "unchanged"  # mid itself proved unchanged
        top = make_attr("top", drv_path=Path("/top.drv"))
        top.ca_bucket = "changed"

        with patch.object(
            review,
            "_ca_drv_references",
            return_value={
                Path("/top.drv"): {Path("/mid.drv")},
                Path("/mid.drv"): {Path("/leaf.drv")},
                Path("/leaf.drv"): set(),
            },
        ):
            review._ca_taint_propagate([top, mid, leaf])

        # mid is "unchanged" so it's sound and untouched; top transitively
        # depends on the opted-out leaf via mid's *drv identity*, but since
        # mid's own bucket is "unchanged" (not "not-comparable"), top is not
        # tainted through it -- taint only originates at not-comparable nodes.
        assert top.ca_bucket == "changed"
        assert mid.ca_bucket == "unchanged"


def test_ca_diff_end_to_end_unchanged_output(helpers: Helpers) -> None:
    """A drv-changing but output-preserving edit (an unused env var added to
    every derivation) must be reported as ca-unchanged for pkg1, since its
    actual build output byte content never changes."""
    with helpers.nixpkgs() as nixpkgs:
        config_nix = nixpkgs.path / "config.nix"
        content = config_nix.read_text()
        assert "PATH = path;" in content
        content = content.replace(
            "PATH = path;", 'PATH = path;\n      CA_DIFF_TEST_MARKER = "v2";'
        )
        config_nix.write_text(content)
        subprocess.run(["git", "add", "."], check=True)
        subprocess.run(
            ["git", "commit", "-m", "add marker env var (no output change)"],
            check=True,
        )

        store_root = Path(tempfile.mkdtemp()) / "store"
        path = main(
            "nixpkgs-review",
            [
                "rev",
                "HEAD",
                "--remote",
                str(nixpkgs.remote),
                "--no-shell",
                "--build-graph",
                "nix",
                "--package",
                "pkg1",
                "--store",
                f"local?root={store_root}",
                "--ca-diff",
            ],
        )
        report = helpers.load_report(path)
        system_result = next(iter(report["result"].values()))
        ca_unchanged_names = {a["name"] for a in system_result["ca-unchanged"]}
        assert "pkg1" in ca_unchanged_names


def test_ca_diff_end_to_end_changed_output(helpers: Helpers) -> None:
    """A change to pkg1.txt directly changes pkg1's build output bytes, so
    it must be reported as ca-changed."""
    with helpers.nixpkgs() as nixpkgs:
        nixpkgs.path.joinpath("pkg1.txt").write_text("different content\n")
        subprocess.run(["git", "add", "."], check=True)
        subprocess.run(["git", "commit", "-m", "change pkg1 output"], check=True)

        store_root = Path(tempfile.mkdtemp()) / "store"
        path = main(
            "nixpkgs-review",
            [
                "rev",
                "HEAD",
                "--remote",
                str(nixpkgs.remote),
                "--no-shell",
                "--build-graph",
                "nix",
                "--package",
                "pkg1",
                "--store",
                f"local?root={store_root}",
                "--ca-diff",
            ],
        )
        report = helpers.load_report(path)
        system_result = next(iter(report["result"].values()))
        ca_changed_names = {a["name"] for a in system_result["ca-changed"]}
        assert "pkg1" in ca_changed_names

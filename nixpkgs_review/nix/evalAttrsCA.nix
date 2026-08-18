{ attr-json }:

# CA-diff variant of evalAttrs.nix: same attribute resolution, but each
# resolved package is (conditionally) turned into a content-addressed
# derivation before its drvPath is read off, so callers can build it and
# compare the realized output path across branches.
#
# A package keeps its own opinion when it has one: fixed-output derivations
# (already content-addressed via their declared hash) and packages that
# already set `__contentAddressed` (opt-in or opt-out) are left untouched.
# `caStatus` tells the caller exactly what happened, since "did not add the
# override" means different things for comparison purposes:
#   - "applied":     we added __contentAddressed; path comparison is valid.
#   - "fod":         already content-addressed via a fixed outputHash;
#                    path comparison is valid without any override.
#   - "already-ca":  package already opted in to __contentAddressed itself;
#                    path comparison is valid.
#   - "opted-out":   package explicitly set __contentAddressed = false;
#                    stays input-addressed, so its path is NOT a reliable
#                    signal of content — not comparable.
#   - "cannot-override": no overrideAttrs to apply CA through (rare,
#                    non-mkDerivation-style derivations) — not comparable.
with builtins;
mapAttrs (
  system: attrs:
  let
    pkgs = import <nixpkgs> {
      inherit system;
      config = import (getEnv "NIXPKGS_CONFIG") // {
        allowBroken = false;
      };
    };

    inherit (pkgs) lib;

    # nix-eval-jobs only shows derivations, so create an empty one to return
    fake =
      extra:
      derivation {
        name = "fake";
        system = "fake";
        builder = "fake";
      }
      // extra;

    withCA =
      pkg:
      let
        maybeHasOutputHash = tryEval (pkg ? outputHash);
        maybeHasCA = tryEval (pkg ? __contentAddressed);
        maybeCAValue = tryEval (pkg.__contentAddressed or false);
        canOverride = pkg ? overrideAttrs;
        hasOutputHash = maybeHasOutputHash.success && maybeHasOutputHash.value;
        hasCA = maybeHasCA.success && maybeHasCA.value;
        caValue = maybeCAValue.success && maybeCAValue.value;
      in
      if hasOutputHash then
        {
          status = "fod";
          pkg = pkg;
        }
      else if hasCA && caValue then
        {
          status = "already-ca";
          pkg = pkg;
        }
      else if hasCA && !caValue then
        {
          status = "opted-out";
          pkg = pkg;
        }
      else if canOverride then
        {
          status = "applied";
          pkg = pkg.overrideAttrs (_: {
            __contentAddressed = true;
            outputHashMode = "recursive";
            outputHashAlgo = "sha256";
          });
        }
      else
        {
          status = "cannot-override";
          pkg = pkg;
        };

    pkgOrFake =
      name: pkg0:
      let
        maybeDerivation = tryEval (lib.isDerivation pkg0);
        maybePath = tryEval pkg0.outPath;
        broken = !maybeDerivation.success || !maybeDerivation.value || !maybePath.success;
      in
      if broken then
        lib.nameValuePair name (
          builtins.addErrorContext "while evaluating the attribute `${name}`" (fake {
            exists = true;
            inherit broken;
            caStatus = "cannot-override";
          })
        )
      else
        let
          ca = withCA pkg0;
        in
        lib.nameValuePair name (
          builtins.addErrorContext "while evaluating the attribute `${name}`" (
            ca.pkg
            // {
              exists = true;
              inherit broken;
              caStatus = ca.status;
            }
          )
        );

    getProperties =
      name:
      let
        attrPath = lib.splitString "." name;
        maybePkg = tryEval (lib.attrByPath attrPath null pkgs);
        pkg = maybePkg.value;
        exists = lib.hasAttrByPath attrPath pkgs;
      in
      # some packages are set to null or throw if they aren't compatible with a platform or package set
      if !maybePkg.success || pkg == null then
        [
          (lib.nameValuePair name (fake {
            inherit exists;
            broken = true;
            caStatus = "cannot-override";
          }))
        ]
      else if !lib.isDerivation pkg then
        if !lib.isAttrs pkg then
          # if it is not a package, ignore it (it is probably something like overrideAttrs)
          [ ]
        else
          lib.flatten (lib.mapAttrsToList (name': _: getProperties "${name}.${name'}") pkg)
      else
        [ (pkgOrFake name pkg) ];
  in
  listToAttrs (concatMap getProperties attrs) // { recurseForDerivations = true; }
) (fromJSON (readFile attr-json))

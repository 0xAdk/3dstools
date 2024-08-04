{
  inputs = {
    utils.url = "github:numtide/flake-utils";
  };
  outputs = {
    self,
    nixpkgs,
    utils,
  }:
    utils.lib.eachDefaultSystem (system: let
      pkgs = nixpkgs.legacyPackages.${system};
    in {
      devShell = pkgs.mkShell {
        buildInputs = with pkgs; [
          python311Full

          python311Packages.pip
          python311Packages.numpy
          python311Packages.pypng
        ];

        shellHook = ''
          export TOOL_PATH=$(realpath .)
        '';

        packages =
          pkgs.lib.forEach
          ["bflim" "msbt" "sarc" "bcfnt" "bffnt"]
          (tool: pkgs.writeShellScriptBin tool ''"$TOOL_PATH/${tool}.py" "$@"'');
      };
    });
}

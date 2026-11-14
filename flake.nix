{
  description = "Price-Aware Thermostat development environment";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  outputs = { nixpkgs, ... }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
    in {
      devShells = nixpkgs.lib.genAttrs systems (system:
        let pkgs = import nixpkgs { inherit system; };
        in {
          default = pkgs.mkShell {
            packages = [ pkgs.python314 pkgs.uv ];
            UV_PYTHON = "${pkgs.python314}/bin/python3.14";
            UV_PYTHON_DOWNLOADS = "never";
            shellHook = ''
              export LD_LIBRARY_PATH="${pkgs.lib.makeLibraryPath [ pkgs.stdenv.cc.cc.lib pkgs.zlib ]}''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
              export PATH="$PWD/.venv/bin:$PATH"
              uv sync --frozen --only-group dev
            '';
          };
        });
    };
}

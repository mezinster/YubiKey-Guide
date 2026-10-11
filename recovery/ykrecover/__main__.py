"""python3 -m ykrecover → GUI; python3 -m ykrecover --cli ... → terminal."""

import sys

LINUX_ONLY = ("ykrecover runs on Linux only (the LiveCD built from nix/, or another Linux system with "
              "gpg and ykman). See recovery/README.md.")


def main() -> int:
    if not sys.platform.startswith("linux"):
        print(f"error: {LINUX_ONLY}", file=sys.stderr)
        return 1
    args = sys.argv[1:]
    if args[:1] == ["--cli"]:
        from .cli import main as cli_main
        return cli_main(args[1:])
    from .gui import main as gui_main
    return gui_main()


if __name__ == "__main__":
    sys.exit(main())

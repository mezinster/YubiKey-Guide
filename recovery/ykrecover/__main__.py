"""python3 -m ykrecover → GUI; python3 -m ykrecover --cli ... → terminal."""

import sys


def main() -> int:
    args = sys.argv[1:]
    if args[:1] == ["--cli"]:
        from .cli import main as cli_main
        return cli_main(args[1:])
    from .gui import main as gui_main
    return gui_main()


if __name__ == "__main__":
    sys.exit(main())

import argparse
from yui.tui import YuiApp


def main() -> None:
    p = argparse.ArgumentParser(
        prog="yui",
        description="YouTube Music TUI powered by Brave",
    )
    p.add_argument(
        "--login",
        action="store_true",
        help="Open a visible Brave window for first-time Google login",
    )
    args = p.parse_args()

    if args.login:
        print("Opening Brave for login. Sign in, then close the window.")
        print()

    YuiApp(visible=args.login).run()


if __name__ == "__main__":
    main()

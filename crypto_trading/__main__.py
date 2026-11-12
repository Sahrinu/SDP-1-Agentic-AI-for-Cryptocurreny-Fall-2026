"""Single command entry point for the project's independent components."""

from __future__ import annotations

import sys


USAGE = "Usage: python -m crypto_trading {market|fundamentals|long-term|stablecoin|replay} [options]"


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print(USAGE)
        return 0 if args else 2
    command, *rest = args
    if command == "market":
        from crypto_trading.main import main as market_main
        market_main(rest)
    elif command == "fundamentals":
        if rest:
            raise SystemExit("fundamentals does not take arguments")
        from crypto_trading.fundamentals_main import main as fundamentals_main
        fundamentals_main()
    elif command == "long-term":
        from crypto_trading.long_term_main import main as long_term_main
        long_term_main(rest)
    elif command == "stablecoin":
        from crypto_trading.stablecoin.__main__ import main as stablecoin_main
        return stablecoin_main(rest)
    elif command == "replay":
        from crypto_trading.replay import main as replay_main
        replay_main(rest)
    else:
        raise SystemExit(f"Unknown command: {command}\n{USAGE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

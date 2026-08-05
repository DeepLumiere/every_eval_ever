"""CLI for converting LEXam leaderboard data to every_eval_ever format."""

import argparse


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            'Fetch LEXam leaderboard data from GitHub and convert it '
            'to Every Eval Ever schema JSON files.'
        )
    )
    parser.add_argument(
        '--output_dir',
        default='data',
        help='Base output directory (default: data).',
    )
    args = parser.parse_args()
    args.source_organization_name = 'unknown'
    args.evaluator_relationship = 'third_party'
    args.source_organization_url = None
    args.source_organization_logo_url = None
    args.eval_library_name = 'lighteval'
    args.eval_library_version = 'unknown'

    from every_eval_ever.cli import _cmd_convert_lexam

    return _cmd_convert_lexam(args)


if __name__ == '__main__':
    raise SystemExit(main())

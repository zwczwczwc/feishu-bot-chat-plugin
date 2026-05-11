#!/usr/bin/env python3
"""
Patch Hermes Agent's Feishu adapter to render ``<at>`` mentions as clickable
@-mentions via Feishu's post-format ``at`` elements.

Without this patch, ``<at>`` tags embedded in outgoing text messages are
rendered as literal plain text rather than as clickable @-mentions.

Usage::

    python3 scripts/patch-hermes-feishu-adapter.py [--path FEISHU_PY_PATH]

The default path assumes Hermes is installed at ``~/.hermes/hermes-agent/``.
After patching, restart the Hermes gateway::

    systemctl --user restart hermes-gateway
    # or: hermes gateway restart
"""

import argparse
import os
import re
import sys

# -------------------------------------------------------------------
# Patch content
# -------------------------------------------------------------------

AT_TAG_REGEX_LINE = '# Detect <at user_id="...">name</at> tags for proper mention rendering\n_AT_TAG_RE = re.compile(r\'<at\\s+user_id="([^"]+)"\\s*>([^<]+)</at>\')\n'

BUILD_POST_WITH_MENTIONS_FN = '''
def _build_post_with_mentions(content: str) -> str:
    """Build a Feishu post payload with explicit ``at`` elements.

    Splits the content at ``<at>`` tags and creates proper Feishu post
    ``at`` elements so mentions render as clickable @ mentions instead
    of plain text.
    """
    if not content:
        return _build_markdown_post_payload(content)

    parts: list[dict[str, str]] = []
    last_end = 0

    for match in _AT_TAG_RE.finditer(content):
        start, end = match.start(), match.end()
        user_id = match.group(1)
        name = match.group(2)

        # Text before this <at> tag
        if start > last_end:
            text_before = content[last_end:start]
            if text_before:
                parts.append({"tag": "md", "text": text_before})

        # The <at> tag itself — use Feishu's at element
        parts.append({"tag": "at", "user_id": user_id})

        # Append the bot name as plain text right after the at element
        # so users see who was @'d
        parts.append({"tag": "md", "text": name + " "})

        last_end = end

    # Any remaining text after the last <at> tag
    if last_end < len(content):
        remaining = content[last_end:]
        if remaining:
            parts.append({"tag": "md", "text": remaining})

    if not parts:
        return _build_markdown_post_payload(content)

    return json.dumps(
        {
            "zh_cn": {
                "content": [parts],
            }
        },
        ensure_ascii=False,
    )


'''


def patch_feishu_py(filepath: str) -> bool:
    """Apply the AT-tag patch to Hermes Feishu adapter."""
    with open(filepath, "r", encoding="utf-8") as f:
        source = f.read()

    changed = False

    # 1. Add _AT_TAG_RE after _MULTISPACE_RE
    if "_AT_TAG_RE" not in source:
        source = source.replace(
            '_MULTISPACE_RE = re.compile(r"[ \\t]{2,}")',
            '_MULTISPACE_RE = re.compile(r"[ \\t]{2,}")\n'
            + AT_TAG_REGEX_LINE,
        )
        changed = True
        print("  ✓ Added _AT_TAG_RE regex")

    # 2. Add _build_post_with_mentions before _build_markdown_post_payload
    if "_build_post_with_mentions" not in source:
        source = source.replace(
            "def _build_markdown_post_payload(content: str) -> str:",
            BUILD_POST_WITH_MENTIONS_FN
            + "def _build_markdown_post_payload(content: str) -> str:",
        )
        changed = True
        print("  ✓ Added _build_post_with_mentions function")

    # 3. Add AT tag check in _build_outbound_payload
    if "# Feed <at> tags through the post pipeline" not in source:
        source = source.replace(
            "if _MARKDOWN_HINT_RE.search(content):",
            "        # Feed <at> tags through the post pipeline with explicit at-elements\n"
            "        # so mentions render as clickable @ mentions (text format shows them\n"
            "        # as literal plain text in some Feishu versions).\n"
            "        if _AT_TAG_RE.search(content):\n"
            "            return \"post\", _build_post_with_mentions(content)\n"
            "        if _MARKDOWN_HINT_RE.search(content):",
        )
        changed = True
        print("  ✓ Added AT-tag check in _build_outbound_payload")

    if changed:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(source)
        print(f"\n✅ Patch applied to {filepath}")
    else:
        print(f"ℹ️  No changes needed — {filepath} already patched")

    return changed


def main():
    parser = argparse.ArgumentParser(
        description="Patch Hermes Feishu adapter for proper @-mention rendering"
    )
    default_path = os.path.expanduser(
        "~/.hermes/hermes-agent/gateway/platforms/feishu.py"
    )
    parser.add_argument(
        "--path",
        default=default_path,
        help=f"Path to feishu.py (default: {default_path})",
    )
    args = parser.parse_args()

    if not os.path.exists(args.path):
        print(f"❌ File not found: {args.path}")
        sys.exit(1)

    print(f"Patching {args.path} ...")
    changed = patch_feishu_py(args.path)

    if changed:
        print("\n🔄 Restart the Hermes gateway for changes to take effect:")
        print("   systemctl --user restart hermes-gateway")


if __name__ == "__main__":
    main()
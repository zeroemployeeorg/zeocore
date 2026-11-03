bump: minor
The `source_item` intervention card accepts a `copy_line` and each `commands` entry of up to 8192 UTF-8 bytes (it was 4096). The limit counts bytes, so 8 KB of ASCII fits and non-ASCII text holds fewer characters. The control-character, line-feed, order and equality rules are unchanged, and no other text field changes.

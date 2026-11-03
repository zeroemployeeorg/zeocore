bump: minor
The `source_item` intervention card gains an optional ordered `commands` list (1 to 12 entries, each one literal copy block, absent rather than null). `copy_line` stays and, when both are present, equals the first command. The React card shows each command in order. Over-limit input is refused, never cut. Existing cards stay valid.

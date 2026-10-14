## Shared specs (../raceflag_specs)

RaceFlag also runs on an ESP32 (separate repo, separate Claude session). Behaviour is kept
in sync through the shared specs repo at ../raceflag_specs.

- Any user-visible change, including a bug fix that changes behaviour, must update in the
  same piece of work: the feature page in features/, contracts/api.md if any /api route
  changes, conformance/ cases if flag decisions change, and the Pi column in PARITY.md.
  Set the ESP32 columns of a changed feature to ❌ (or 🚧) so the ESP32 session sees it.
- When porting a feature from the ESP32, work from its spec and conformance cases, not
  from the ESP32 code.
- Never modify the ESP32 repo.
- Commit spec changes in raceflag_specs as their own commit, with a message naming the feature.

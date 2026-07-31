# Cluster Lab compatibility guide

> Deprecated for new work. The canonical laboratory is the versioned Story/Trend Engine described
> in [`TREND_ENGINE.md`](../TREND_ENGINE.md).

`cluster_lab.db` was the first sandbox for testing canonical URLs, title guards and heuristic
proposals without mutating production story tables. It established the correct development rule:
small immutable local slices before a full run.

It also had three structural limitations:

- a release stored references to a live `compass.db`, not full frozen rows;
- rerunning an experiment could replace previous proposal output;
- Story and Trend versions were not independent publication units.

The replacement is `trend_engine.db`:

```text
compass.db (read-only corpus)
  ↓ full frozen copy + checksum
DataRelease → FacetRelease → StoryRelease → TrendRelease → RadarPublication
```

The old CLI remains for one transition release and prints a deprecation warning:

```bash
reddit-compass lab ...
```

Do not create new production experiments with it. Safe metadata migration is:

```bash
reddit-compass engine legacy \
  --lab-db data/cluster_lab.db \
  --source-db data/compass.db
```

Migration imports a legacy release only when its recorded source checksum still matches. Legacy
experiments are marked `requires_rerun`; their proposals are not silently promoted or reinterpreted.

New workflow:

```bash
reddit-compass engine release create --run RUN_ID
reddit-compass engine facets --release RELEASE_ID --profile broad
reddit-compass engine stories propose --facet-release FACET_ID --limit 50
reddit-compass engine stories inspect --story-release STORY_ID
reddit-compass engine trends propose --story-release STORY_ID --window 30d
```

Publication and rollback now change an immutable channel pointer. Backup tables and full database
rebuilds are not part of the development loop.

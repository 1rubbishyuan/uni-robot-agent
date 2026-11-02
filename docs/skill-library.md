# Shared and environment skill library

The packaged root is `uni_robot_agent/resources/skills/`. Every session reads
`general/` and its environment namespace (`metadata.skill_namespace`, otherwise
the integration name). A RoboDojo session reads both `general/` and `robodojo/`.
Other sessions cannot read RoboDojo-private guidance. Discovery does not create
missing directories. External libraries replace the complete root; they do not
overlay installed files. See [configuration](running.md#skills).

General skills describe reusable methods and state their capability assumptions.
They may use uni-robot-agent tool contracts, but do not depend on an environment's
private paths, robot model or benchmark scoring rules. Environment skills explain
robot geometry, control behavior, observations and finishing conditions. They
reference shared methods rather than duplicating them. Neither scope stores
fixed task solutions.

The agent can read both scopes, but optional `write_skill` writes only within
its environment namespace in an explicitly selected external root. Shared
`general/` guidance is maintained through reviewed source changes.

## Layout

```text
skills/
├── general/
│   ├── SKILL.md
│   ├── index.md
│   └── gripper/
│       ├── contact-effect/
│       │   ├── SKILL.md
│       │   └── references/
│       │       └── exact-count-cycles.md
│       ├── contents-transfer/
│       │   └── SKILL.md
│       ├── deformable-material/
│       │   └── SKILL.md
│       ├── grasp-geometry/
│       │   ├── SKILL.md
│       │   └── references/
│       │       ├── contact-calculation.md
│       │       └── shape-based-grasping.md
│       ├── handover/
│       │   └── SKILL.md
│       ├── held-object-geometry/
│       │   └── SKILL.md
│       ├── motion-recovery/
│       │   └── SKILL.md
│       ├── moving-target-control/
│       │   └── SKILL.md
│       ├── placement-and-engagement/
│       │   └── SKILL.md
│       ├── pose-targeting/
│       │   └── SKILL.md
│       ├── reachability-and-recovery/
│       │   └── SKILL.md
│       ├── return-to-start/
│       │   └── SKILL.md
│       ├── scene-memory/
│       │   ├── SKILL.md
│       │   └── references/
│       │       └── pose-and-order-memory.md
│       ├── state-verification/
│       │   └── SKILL.md
│       ├── supported-contact/
│       │   └── SKILL.md
│       └── tool-selection/
│           ├── SKILL.md
│           └── references/
│               ├── depth-grounding.md
│               └── rgb-grounding.md
└── robodojo/
    ├── index.md
    ├── perception-reference.md
    ├── robot-reference.md
    └── gripper/
        ├── motion-control/
        │   └── SKILL.md
        └── return-to-start/
            └── SKILL.md
```

## Organizing an external library

Use the complete namespace layout above. Put reusable guidance in `general/`
and X5 geometry, camera conventions and native control details in `robodojo/`.
Update indices and links when moving your own files, and preserve the library
snapshot used by existing runs. See [skills setup](running.md#skills).

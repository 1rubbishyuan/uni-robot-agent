DexJoCo provides one actor-controlled arm and dexterous hand assembly. Use the
control groups declared by actor_act. Each group couples its arm TCP and all
sixteen hand joints in one native control step. It is not a scalar gripper.

Give actor_act a concrete, bounded local instruction consistent with the task.
Use fresh scene observations between calls to decide whether to continue, refine
the instruction or finish. A completed chunk means its control samples executed;
it does not mean the requested manipulation succeeded.

The reviewer can allow a student prefix or stop and return control. It cannot
edit TCP poses, fingers or individual predictions. Direct motion tools and EEF
corrections are unavailable. constraints must be empty for this binding.

Samples represent simulated control time. Inference and review pause simulation;
wall-clock latency is not native motion duration. Shorter prefixes reobserve
more often. Unused predictions are discarded before the next inference.

Only pick_bucket with the published single-arm model recipe is currently an
admitted runtime. Do not infer support for other tasks or robot layouts from
similar names or from a model's general language capabilities.

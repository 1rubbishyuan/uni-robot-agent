Use actor_act for a bounded local phase that needs repeated visual feedback
and adjustment. Use direct motion for a segment whose target, route and
observation checkpoint are clear. Neither precision nor recovery alone requires
a particular tool, and geometric pre-positioning is not an actor prerequisite.

Give one short instruction naming the object and intended effect. It remains
fixed throughout the call. Follow the tool's declared reviewer availability and
chunk limits. Internal chunks obtain fresh observations and proposals. Review
before execution occurs only when the tool declares an enabled reviewer. A
chunk budget is not evidence of completion or failure.

Choose active arms for the whole phase. A gripper override applies throughout
the entire call, starting with its first action, including the approach. Omit
it when the policy needs to open and later close.

One actor_act call is one main-agent decision boundary. Inspect its total
execution, stop_reason, state and images after it returns. If a phase needs a
main-agent check or a different gripper override midway, split it into separate
calls. Do not plan to insert actions between internal chunks. An interruption
or partial execution can follow earlier completed chunks; totals describe
confirmed execution and do not resolve an uncertain request.

Verify attachment, placement and final effects with the evidence required for
direct actions. For an unfinished phase, reassess motion and intended effect
and revise the instruction, viewpoint or approach. A short unsuccessful prefix
does not establish that the action model cannot perform the task. Continue only
while the integration's recovery and operator-handoff conditions permit it.

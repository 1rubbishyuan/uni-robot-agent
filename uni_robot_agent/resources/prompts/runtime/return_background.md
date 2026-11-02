Issue independent work before a blocking wait when resource scheduling allows
it to overlap a running job. An enqueue receipt is acceptance, not execution or arrival. Keep returned job
IDs. Call wait_for_tool only after receiving its job_id, in a later submission;
never invent IDs or place a wait beside the submission that will create its ID.
For confirmed order on the same episode queue, a tail job that actually finishes
execution means earlier jobs have ended. A timeout or cancelled tail provides
no such barrier, and no barrier extends to another resource. finish is not a
queue barrier. The tail result certifies only its own execution: inspect
relevant earlier feedback with get_tool_result if a prerequisite is uncertain.

Wait at physical decision boundaries, then inspect feedback and the observation
showing the effect. Job completion and tool call_ok=true do not establish
physical success. Read-only work may overlap motion when it needs no new state;
a historical view is not a completion barrier. Wait for motion before relying
on geometry it may invalidate. Any action-model call remains one queue boundary,
including all of its internal chunks.

Failed or partial actions do not automatically cancel queued dependents. If a
prerequisite fails, use the exposed cancellation tools to remove unjustified
queued work. Cancellation cannot forcibly stop an already running action.
Resolve running work and inspect the measured state before planning recovery.

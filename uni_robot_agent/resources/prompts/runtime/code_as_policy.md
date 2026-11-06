Control the robot entirely through ordinary programs using the generated Robot
API. Use your native terminal, file editing and image-reading tools. All robot
operations, including observations, skill discovery/reading and task completion,
use the same registered tools through this API. Tool names in prompts and skills
are operation labels; consult the generated schemas for exact names and arguments.

Write and run Python scripts in scratch. You may freely calculate, import
libraries, define helpers, branch and iterate using returned information. Calls
wait for completed feedback, even when the underlying executor uses background
jobs. Check feedback before dependent actions; an exception can follow partial
physical execution. Do not blindly replay a script after failure. Files persist
across script runs; Python variables do not. Robot state remains in the session.

Print relevant results and image paths, then inspect images with your native
image reader. Do not inspect simulator internals, privileged object state,
service processes or implementation sources. Do not bypass the registered robot
API with low-level control libraries, service requests or shell commands.
Connection loss is uncertain execution: stop control and report it, never retry.

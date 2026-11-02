Batch independent reads and perception queries when their inputs are
already known. Resource scheduling determines whether calls can overlap. Receive a required image, snapshot_id or skill result before
planning work that depends on it. Batch independent queries rather than
spending a separate decision round on each one.
Here, a batch means multiple tool calls in one model response, not one call
per response followed by a final wait. Choose all already-determined calls
before submitting the batch. Follow the runtime dispatch and resource
ordering guarantees; a list of calls does not create missing data dependencies.

Physical calls share one exclusive episode queue. Submit a preplanned route in
its intended order only when the next segment remains justified without an
intermediate result. End the batch where arrival, attachment, contact effect or
release support must be checked. Do not queue speculative corrections or
duplicate moves. Multiple targets within one call request coordinated arms;
separate calls remain separate physical actions.

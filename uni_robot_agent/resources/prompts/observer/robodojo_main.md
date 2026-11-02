RoboDojo publishes snapshots after completed primitive steps. During a
background high-level action, an observer may see an earlier snapshot or a
newly completed primitive marked in_flight. These can show intermediate changes;
verify the corresponding completed action before relying on its final effect.

Direct results contain state, feedback and the final RGB snapshot_id.
move/grasp/release attach the final head image and participating arms'
wrist images when available; hold attaches one final head image. These RGB
records have no depth or calibration. actor_act attaches its latest snapshot's views.
Use view_env_state for additional or recorded views and capture_scene when
calibrated geometry is needed. Evidence from before closure, lift or release
does not verify that action's effect.

from tests.common.solver import Solver
import b3d
from b3d import Pose
from b3d.chisight.dense.dense_only_patch_tracking.model import rr_log_uniformpose_meshes_to_image_model_trace
import b3d.chisight.dense.dense_only_patch_tracking.patch_tracking as tracking
import jax.numpy as jnp
import rerun as rr

class AdamPatchTracker_UsingDenseOnlyTraces(Solver):
    # Coordinate frames:
    # W - world
    # C - camera
    # P - patch (used to store mesh vertex positions in the patch frame)
    #
    # Note that the patch model trace we generate here is told to use the camera
    # frame as it's world frame; this function needs to handle
    # conversion from the trace's world frame (our camera frame)
    # to the task's world frame (our world frame).

    def __init__(self):
        self.get_trace = None
        self.all_positions_C = None
        self.all_quaternions_C = None
        self.all_positions_W = None
        self.all_quaternions_W = None
        self.mesh = None
        self.poses_CP_at_time0 = None

    def solve(self, task_spec):
        video = task_spec["video"]
        assert video.shape[-1] == 4, "Expected video to be RGBD"
        poses_WC = task_spec["poses_WC"]
        initial_patch_centers_2D = task_spec["initial_keypoint_positions_2D"]
        r = task_spec["renderer"]
        fx, fy, cx, cy = r.fx, r.fy, r.cx, r.cy

        (patch_vertices_P, patch_faces, patch_vertex_colors, poses_CP_at_time0, _) = tracking.get_patches(
            initial_patch_centers_2D, video, Pose.identity(), fx, fy, cx, cy
        )
        self.mesh = (patch_vertices_P, patch_faces, patch_vertex_colors)
        self.poses_CP_at_time0 = poses_CP_at_time0

        model = tracking.get_default_multiobject_model_for_patchtracking(r)
        (get_initial_tracker_state, update_tracker_state, get_trace) = tracking.get_adam_optimization_patch_tracker(
            model, patch_vertices_P, patch_faces, patch_vertex_colors
        )
        self.get_trace = get_trace
        tracker_state = get_initial_tracker_state(poses_CP_at_time0)
        self.all_positions_C = []
        self.all_quaternions_C = []
        self.all_positions_W = []
        self.all_quaternions_W = []
        observed_rgbds = video

        N_FRAMES = observed_rgbds.shape[0]
        for timestep in range(N_FRAMES):
            (pos_C, quats_C), tracker_state = update_tracker_state(tracker_state, observed_rgbds[timestep])
            pose_W = poses_WC[timestep] @ Pose(pos_C, quats_C)
            self.all_positions_W.append(pose_W.pos)
            self.all_quaternions_W.append(pose_W.xyzw)
            self.all_positions_C.append(pos_C)
            self.all_quaternions_C.append(quats_C)

        keypoints_3D_C = jnp.stack(self.all_positions_C)
        inferred_keypoints_2D = b3d.camera.screen_from_camera(
            keypoints_3D_C, r.get_intrinsics_object()
        )[:, :, ::-1]
        return inferred_keypoints_2D
    
    def visualize_solver_state(self, task_spec):
        pos0_C = self.poses_CP_at_time0.pos
        quat0_C = self.poses_CP_at_time0.xyzw
        trace = self.get_trace(pos0_C, quat0_C, task_spec["video"][0])
        rr_log_uniformpose_meshes_to_image_model_trace(
            trace, task_spec["renderer"], prefix="patch_tracking_initialization",
            timeless=True,
            # Viz uses World coordinate frame; trace uses Camera coordinate frame.
            # Hence, we need this transformation.
            transform_Viz_Trace=task_spec["poses_WC"][0]
        )

        for t in range(len(self.all_positions_W)):
            rr.set_time_sequence("frame", t)
            trace = self.get_trace(self.all_positions_C[t], self.all_quaternions_C[t], task_spec["video"][t])
            rr_log_uniformpose_meshes_to_image_model_trace(
                trace, task_spec["renderer"], prefix="PatchTrackingTrace",
                # Viz uses World coordinate frame; trace uses Camera coordinate frame.
                # Hence, we need this transformation.
                transform_Viz_Trace=task_spec["poses_WC"][t]
            )
            del trace
import math
import uuid
import numpy as np
from gym import spaces
from micoenv.Mico import Mico
from micoenv.arm_randomizer import create_randomized_description
from micoenv.bullet_robot_env import BulletRobotEnv
import os
import sys

tmp_dir = os.path.dirname(sys.modules['__main__'].__file__) + "/tmp"


def goal_distance(goal_a, goal_b):
    assert goal_a.shape == goal_b.shape
    return np.linalg.norm(goal_a - goal_b, axis=-1)


class MicoEnv(BulletRobotEnv):
    def __init__(
            self,
            n_substeps=5,
            has_object=True,
            target_in_the_air=True,
            distance_threshold=0.1,
            height_offset=0.06,
            reward_type="positive",
            done_after=float("inf"),
            observation_type="low_dim",
            fixed_goal=True,
            use_gui=False,
            randomize_textures=False,
            normal_textures=False,
            randomize_objects=False,
            randomize_camera=False,
            randomize_arm=False,
    ):
        self.randomizeCamera = randomize_camera
        self.randomizeArm = randomize_arm
        self.envId = uuid.uuid4()
        self.randomizeTextures = randomize_textures
        self.has_object = has_object
        self.target_in_the_air = target_in_the_air
        self.normalTextures = normal_textures
        self.randomizeObjects = randomize_objects
        self.fixed_goal = fixed_goal
        self.low_dim_space = spaces.Box(
            -np.inf, np.inf, shape=(22,), dtype="float32")
        self.distance_threshold = distance_threshold
        self.reward_type = reward_type
        self.height_offset = height_offset
        self.table_low = [-0.35, -0.25, 0.05]
        self.table_high = [-0.2, 0.25, 0.2]
        self._max_episode_steps = done_after
        self.observation_type = observation_type
        self.n_actions = 4
        super(MicoEnv, self).__init__(
            n_substeps=n_substeps,
            n_actions=self.n_actions,
            use_gui=use_gui,
            observation_type=observation_type,
            done_after=done_after,
        )
        self.goalShape = self.p.createCollisionShape(
            self.p.GEOM_SPHERE, radius=0.03)
        self.reset()
        self.state_dim = self.state_vector().shape
        high = np.inf * np.ones(self.state_dim)
        low = -high
        aux_high = np.ones((16,)) * 10
        self.state_space = spaces.Box(low, high)
        self.aux_space = spaces.Box(-aux_high, aux_high)

    def compute_reward(self, achieved_goal, desired_goal):
        d = goal_distance(achieved_goal, desired_goal)
        if self.reward_type == "sparse":
            return 3 if (d < self.distance_threshold) else -1
        elif self.reward_type == "positive":
            return 5 if (d < self.distance_threshold) else 0

    def _step_callback(self):
        self.arm.step_simulation()

    def _set_action(self, action):
        assert action.shape == (self.n_actions,)
        action = np.clip(action, -1, 1)
        action[3] = 0
        action = (
                action.copy() * 0.05
        )
        self.arm.apply_action(action)

    def state_vector(self):
        grip_state = self.p.getLinkState(
            self.arm.armId, 6, computeLinkVelocity=1)
        grip_velp = np.array(grip_state[6])
        grip_pos = np.array(grip_state[0])
        if self.has_object:
            obj_pos = np.array(
                self.p.getBasePositionAndOrientation(self.body)[0])
            object_rel_pos = obj_pos - grip_pos
        else:
            obj_pos = object_rel_pos = np.zeros((3,))
        gripper_state = [
            self.p.getJointState(self.arm.armId, 7)[0],
            self.p.getJointState(self.arm.armId, 9)[0],
        ]
        is_grasping = 1 if self.arm.is_grasping() else 0
        low_dim = np.concatenate([
            grip_pos.copy(),
            self.goal.copy(),
            gripper_state.copy(),
            obj_pos,
            object_rel_pos,
            self.arm.goalPosition,
            [self.arm.goalGripper],
            grip_velp,
            [is_grasping],
        ])
        return low_dim.astype(np.float32)

    def _get_obs(self):
        low_dim = self.state_vector()
        if "pixels" in self.observation_type or "composed" in self.observation_type:
            pixels = self.render(mode="rgb_array")
            assert pixels.shape == (84, 84, 3)

        if self.observation_type == "low_dim":
            return low_dim
        elif self.observation_type == "pixels":
            return pixels
        elif self.observation_type == "pixels_depth":
            return pixels[:, :, 3]
        elif self.observation_type == "composed":
            return {"low_dim": low_dim, "pixels": pixels}
        else:
            raise Exception("Unsupported observation type")

    def _reset_sim(self, state=None, state_file=None):
        if state is not None:
            assert state.shape == (22,)
            grip_pos, goal, gripper_state, obj_pos, _, arm_goal_pos, arm_goal_grip, _, should_be_grasping = (
                state[0:3],
                state[3:6],
                state[6:8],
                state[8:11],
                state[11:14],
                state[14:17],
                state[17],
                state[18:21],
                state[21],
            )
            self.goal = goal
        else:
            high = self.table_high.copy()
            if self.fixed_goal:
                high[1] = 0.0
            self.goal = self._sample_goal()
            obj_pos = self.np_random.uniform(self.table_low, high, size=3)
            obj_pos[2] = self.height_offset
            while goal_distance(obj_pos, self.goal) < 0.1:
                # Recreate te object so it is far enough from the goal and the task
                # is not immediately done.
                obj_pos = self.np_random.uniform(self.table_low, high, size=3)
                obj_pos[2] = self.height_offset
            arm_goal_pos = None
            should_be_grasping = False
            arm_goal_grip = None

        self.p.resetSimulation()
        radius = 0.025
        if self.randomizeObjects:
            radius = np.random.uniform(0.02, 0.03, 1)
        self.goalShape = self.p.createCollisionShape(
            self.p.GEOM_SPHERE, radius=radius)
        self.visualizeTarget = self.p.createMultiBody(
            0.0,
            self.goalShape,
            basePosition=self.goal,
            useMaximalCoordinates=1)
        color = [0.2, 0.2, 0.8, 1]
        if self.randomizeObjects:
            color = np.random.uniform([0.1, 0.1, 0.7, 1], [0.3, 0.3, 1, 1],
                                      (4,))
        self.p.changeVisualShape(self.visualizeTarget, -1, rgbaColor=color)
        self.init_env()
        self.init_arm()
        if self.has_object:
            obj_size = 0.03
            obj_color = [1, 0.3, 0.3, 1]
            if self.randomizeObjects:
                # Make the object some shade of red.
                obj_color = np.random.uniform([0.7, 0.1, 0.1, 1],
                                              [1, 0.3, 0.3, 1], (4,))
                obj_size = np.random.uniform(0.025, 0.04)
            col_shape = self.p.createCollisionShape(
                self.p.GEOM_BOX, halfExtents=[obj_size] * 3)
            self.body = self.p.createMultiBody(
                baseMass=0.3,
                baseCollisionShapeIndex=col_shape,
                basePosition=obj_pos,
                baseOrientation=self.p.getQuaternionFromEuler(
                    [0, 0, np.random.uniform(0, math.pi * 2, 1)]),
            )
            self.originalObjPosition = obj_pos

            self.p.changeVisualShape(self.body, -1, rgbaColor=obj_color)
            self.arm.set_graspable_object(self.body, should_be_grasping)

        if state_file:
            try:
                self.p.restoreState(fileName=state_file)
            except:
                print("Warning: state reset failed")
                return False

        if arm_goal_pos is not None:
            self.arm.goalPosition = arm_goal_pos
            self.arm.goalGripper = arm_goal_grip
        self.originalGoalPosition = self.goal.copy()
        grip_state = self.p.getLinkState(
            self.arm.armId, 6, computeLinkVelocity=1)
        grip_pos = np.array(grip_state[0])
        self.originalGripPosition = grip_pos
        return True

    def _sample_goal(self):
        if self.fixed_goal:
            if self.target_in_the_air:
                return np.array([-0.4, 0.2, 0.3])
            else:
                return np.concatenate(
                    [np.random.normal([-0.3, 0.2], 0.05), [0.03]])
        goal = self.np_random.uniform(self.table_low, self.table_high, size=3)
        goal[2] = 0.03
        if self.target_in_the_air and self.np_random.uniform() < 1:
            goal[2] += self.np_random.uniform(0, self.table_high[2])

        return goal.copy()

    def draw_goal(self):
        self.p.resetBasePositionAndOrientation(self.visualizeTarget, self.goal,
                                               [0, 0, 0, 1])

    def _is_success(self, _):
        grip_state = self.p.getLinkState(
            self.arm.armId, 6, computeLinkVelocity=1)
        grip_pos = np.array(grip_state[0])
        d = goal_distance(grip_pos, self.goal)
        if self.has_object:
            obj_pos = np.array(
                self.p.getBasePositionAndOrientation(self.body)[0])

            d = goal_distance(obj_pos, self.goal)
        return d < self.distance_threshold

    def _env_setup(self, initial_qpos):
        while not self._reset_sim():
            pass

    def _get_reward(self):
        grip_state = self.p.getLinkState(
            self.arm.armId, 6, computeLinkVelocity=1)
        grip_pos = np.array(grip_state[0])
        if self.has_object:
            obj_pos = np.array(
                self.p.getBasePositionAndOrientation(self.body)[0])

            r = self.compute_reward(obj_pos, self.goal)
        else:
            r = self.compute_reward(grip_pos, self.goal)
        return r

    def init_arm(self):
        reach_low = np.array(self.table_low)
        reach_high = np.array(self.table_high)
        reach_low, reach_high = reach_low - 0.2, reach_high + 0.2
        reach_low[2] = 0.05
        spawn_pos = [0, 0, 0]
        if self.randomizeArm:
            spawn_pos = np.clip([0, 0, 0.03], [0, 0, 0.07], np.random.normal([0, 0, 0.05], 0.02))
            urdf, link_color, ring_color = create_randomized_description()
            self.arm = Mico(
                self.p,
                spawn_pos=spawn_pos,
                reach_low=reach_low,
                reach_high=reach_high,
                randomize_arm=self.randomizeArm,
                urdf=urdf,
            )
        else:
            self.arm = Mico(
                self.p,
                spawn_pos=spawn_pos,
                reach_low=reach_low,
                reach_high=reach_high,
                randomize_arm=self.randomizeArm,
            )
            ring_color = [0.4, 0.4, 0.4]
            link_color = [0.1, 0.1, 0.1]

        self.p.changeVisualShape(self.arm.armId, 6, rgbaColor=[0, 0, 0, 0])
        self.p.changeVisualShape(
            self.arm.armId, 7, rgbaColor=np.concatenate([ring_color, [1]]))
        self.p.changeVisualShape(
            self.arm.armId, 9, rgbaColor=np.concatenate([ring_color, [1]]))

        col_shape = self.p.createCollisionShape(
            self.p.GEOM_BOX, halfExtents=[0.08, 0.10, 0.02])
        mount = self.p.createMultiBody(0, col_shape)
        self.p.changeVisualShape(
            mount, -1, rgbaColor=np.concatenate([link_color, [1]]))
        col_shape = self.p.createCollisionShape(
            self.p.GEOM_BOX, halfExtents=[0.02, 0.02, 0.1])
        mount = self.p.createMultiBody(0, col_shape)
        self.p.changeVisualShape(
            mount, -1, rgbaColor=np.concatenate([link_color, [1]]))

        self.arm.init_gripper()

    def init_env(self):

        self.wallId = self.p.loadURDF(
            "plane.urdf",
            [0.697, 0, 0],
            self.p.getQuaternionFromEuler([0, -math.pi / 2, 0]),
        )
        self.planeId = self.p.loadURDF(
            "plane.urdf", [0, 0, 0], self.p.getQuaternionFromEuler([0, 0, 0]))

        if self.randomizeCamera:
            x = np.random.normal(-1.05, 0.04, 1)
            z = np.random.normal(0.68, 0.04, 1)
            lookat_x = np.random.normal(0.1, 0.02, 1)
            pos = [x, 0, z]
            lookat = [lookat_x, 0, 0]
            vec = [-0.5, 0, 1]
            self.viewMatrix = self.p.computeViewMatrix(pos, lookat, vec)
            fov = np.random.normal(45, 2, 1)
            self.projMatrix = self.p.computeProjectionMatrixFOV(
                fov=fov, aspect=4. / 3., nearVal=0.01, farVal=2.5)

            direction = np.array([
                np.random.choice([
                    np.random.random_integers(-20, -5),
                    np.random.random_integers(5, 20),
                ]),
                np.random.choice([
                    np.random.random_integers(-20, -5),
                    np.random.random_integers(5, 20),
                ]),
                np.random.random_integers(70, 100),
            ])

            self.light = {
                "diffuse": np.random.uniform(0.4, 0.6),
                "ambient": np.random.uniform(0.4, 0.6),
                "spec": np.random.uniform(0.4, 0.7),
                "dir": direction,
                "col": np.random.uniform([0.9, 0.9, 0.9], [1, 1, 1]),
            }
        if self.randomizeTextures:
            from micoenv import perlin_noise as noise

            if self.normalTextures:
                wood_color = np.random.normal([170, 150, 140], 8)
                wall_color = np.random.normal([230, 240, 250], 8)
            else:
                wood_color = np.random.uniform([100, 100, 100],
                                               [130, 255, 130])
                wall_color = np.random.uniform([100, 100, 100],
                                               [130, 255, 130])
            tex1 = self.p.loadTexture(
                noise.createAndSave(
                    tmp_dir + "/wall-{}.png".format(self.envId),
                    "cloud",
                    wall_color,
                ))
            tex2 = self.p.loadTexture(
                noise.createAndSave(
                    tmp_dir + "/table-{}.png".format(self.envId),
                    "cloud",
                    wood_color,
                ))
            self.p.changeVisualShape(self.planeId, -1, textureUniqueId=tex2)
            self.p.changeVisualShape(self.wallId, -1, textureUniqueId=tex1)

    def get_state(self):
        return self.state_vector()

    def get_aux(self):
        grip_state = self.p.getLinkState(
            self.arm.armId, 6, computeLinkVelocity=1)
        return np.concatenate([
            self.arm.get_joint_poses(),
            np.array(grip_state[0]), self.arm.goalPosition
        ])

    def store_state(self, fn):
        self.p.saveBullet(fn)

// Adapter hooks contain runtime identity; robot schemas remain unchanged.
export const RobotBridge = async () => ({
  "experimental.chat.system.transform": async (_input, output) => {
    output.system = [process.env.UNI_ROBOT_AGENT_SYSTEM_PROMPT];
  },
  "tool.execute.before": async (input, output) => {
    if (input.tool.startsWith("ura_")) {
      if (!input.callID) throw new Error("OpenCode omitted its tool call ID");
      output.args._uni_robot_agent_call = input.callID;
    }
  },
});

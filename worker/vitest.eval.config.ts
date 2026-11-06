import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["evals/**/*.eval.ts"],
    fileParallelism: false,
    testTimeout: 900_000,
  },
});

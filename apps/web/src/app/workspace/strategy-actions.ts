"use server";

import {
  checkStrategyParameters,
  checkStrategySource,
  persistStrategy,
  validateStrategyVersion,
} from "../../features/auth/server";

export async function saveStrategy(
  input: Parameters<typeof persistStrategy>[0],
) {
  return persistStrategy(input);
}

export async function validateStrategyParameters(
  strategyId: string,
  version: number,
  parameters: Record<string, unknown>,
) {
  return checkStrategyParameters(strategyId, version, parameters);
}

export async function validateStrategyCode(
  code: string,
  dependencies: Record<string, string>,
) {
  return checkStrategySource(code, dependencies);
}

export async function markStrategyVersionValidated(
  strategyId: string,
  version: number,
) {
  return validateStrategyVersion(strategyId, version);
}

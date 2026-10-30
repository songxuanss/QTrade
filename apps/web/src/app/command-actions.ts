"use server";

import { workspaceCommandInstruments } from "../features/auth/server";

export async function searchCommandInstruments(query: string) {
  return workspaceCommandInstruments(query);
}

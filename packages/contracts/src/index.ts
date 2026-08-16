import createClient from "openapi-fetch";

import type { paths } from "./generated/schema.js";

export type { components, operations, paths } from "./generated/schema.js";

export const createTallysteadClient = (options: Parameters<typeof createClient<paths>>[0]) =>
  createClient<paths>(options);

export type TallysteadClient = ReturnType<typeof createTallysteadClient>;

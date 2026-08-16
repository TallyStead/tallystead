import createClient from "openapi-fetch";
import type { paths } from "./generated/schema.js";
export type { components, operations, paths } from "./generated/schema.js";
export declare const createTallysteadClient: (options: Parameters<typeof createClient<paths>>[0]) => import("openapi-fetch").Client<paths, `${string}/${string}`>;
export type TallysteadClient = ReturnType<typeof createTallysteadClient>;
//# sourceMappingURL=index.d.ts.map
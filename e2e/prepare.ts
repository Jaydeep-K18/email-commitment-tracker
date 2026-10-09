/** Rebuild the end-to-end database before a run. */
import { databaseUrl, rebuild } from "./db";

await rebuild();
console.log(`end-to-end database ready: ${new URL(databaseUrl()).pathname.slice(1)}`);

import dotenv from 'dotenv';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __dirname = dirname(fileURLToPath(import.meta.url));
export const rootEnvPath = join(__dirname, '../../.env');
export const backendEnvPath = join(__dirname, '../.env');

dotenv.config({ path: rootEnvPath, override: true });
dotenv.config({ path: backendEnvPath, override: true });

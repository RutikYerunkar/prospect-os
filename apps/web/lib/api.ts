const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`);
  if (!response.ok) {
    throw new ApiError(response.status, `GET ${path} failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export interface HealthResponse {
  status: string;
  mode: "demo" | "live";
  version: string;
}

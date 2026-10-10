// Supabase Auth. The JWT carries app_metadata.company_id; the API trusts nothing else.
import { createClient, type SupabaseClient } from "@supabase/supabase-js";

const url = import.meta.env.VITE_SUPABASE_URL as string | undefined;
const anon = import.meta.env.VITE_SUPABASE_ANON_KEY as string | undefined;
const devToken = import.meta.env.VITE_DEV_TOKEN as string | undefined;

export const supabase: SupabaseClient | null = url && anon ? createClient(url, anon) : null;

export async function getToken(): Promise<string | null> {
  if (devToken) return devToken;
  if (!supabase) return null;
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token ?? null;
}

export async function signIn(email: string, password: string) {
  if (!supabase) throw new Error("Supabase is not configured");
  const { error } = await supabase.auth.signInWithPassword({ email, password });
  if (error) throw error;
}

export async function signOut() {
  await supabase?.auth.signOut();
}

export const usesDevToken = Boolean(devToken);

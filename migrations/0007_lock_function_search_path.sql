-- 0007: pin the search_path of the tenancy helper functions to empty,
-- so they cannot be hijacked by objects in another schema (Supabase security advisor).
ALTER FUNCTION meta.current_company() SET search_path = '';
ALTER FUNCTION meta.is_system() SET search_path = '';

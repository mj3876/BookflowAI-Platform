-- 004_users_auth_pod_setup.sql
-- Schema v3 → v4 migration:
--   1. users.created_at 컬럼 추가 (auth-pod 가 첫 로그인 시 INSERT 시 사용)
--   2. auth_pod role 권한 확장:
--      - INSERT ON users (신규 사용자 자동 provisioning)
--      - UPDATE (email, display_name, last_login_at) ON users (callback 마다 정보 갱신)
--      - SELECT ON users (whoami / login flow)
--
-- 적용: ansible-playbook playbooks/rds-schema.yml + rds-grants.yml
--   또는 직접: psql ... -f 004_users_auth_pod_setup.sql

BEGIN;

-- 1. users.created_at 컬럼 추가 (idempotent)
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- 2. auth_pod 권한 확장 (기존: UPDATE last_login_at + INSERT audit_log)
GRANT SELECT                                            ON users TO auth_pod;
GRANT INSERT                                            ON users TO auth_pod;
GRANT UPDATE (email, display_name, last_login_at)       ON users TO auth_pod;

-- 3. schema_versions 마커 (있으면)
INSERT INTO schema_versions (version, applied_at, description)
VALUES ('v4', NOW(), 'users.created_at + auth_pod RBAC for OIDC self-provisioning')
ON CONFLICT (version) DO NOTHING;

COMMIT;

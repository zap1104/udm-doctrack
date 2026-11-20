-- The installer creates this private schema and grants the web login EXECUTE.
-- The table owner reads the route and grant tables here to avoid recursive policies.
CREATE OR REPLACE FUNCTION doctrack_private.actor(area text, operation text)
RETURNS TABLE(id bigint, office_id bigint, role text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT u.id::bigint, u.office_id::bigint, u.role::text FROM public.accounts_user u
    WHERE u.id = nullif(current_setting('doctrack.actor_id', true), '')::bigint
      AND u.is_active AND u.role IN ('SYSTEM_ADMIN','ADMIN','USER','VIEWER')
      AND (u.office_id IS NOT NULL OR u.role = 'SYSTEM_ADMIN')
      AND (operation IN ('view_record','view_content') OR u.role <> 'VIEWER')
      AND NOT EXISTS (
          SELECT FROM public.accounts_userpermissionpolicy p
          JOIN public.accounts_capabilityrestriction b ON b.policy_id = p.id
          WHERE p.user_id = u.id AND b.area = $1 AND (
              b.action IN ('view_record', $2) OR (operation NOT IN ('view_record','view_content') AND (
                current_setting('doctrack.operation',true) = $1 || ':' || b.action OR (
                b.action='view_content' AND current_setting('doctrack.operation',true) IN (
                  $1 || ':edit_files', $1 || ':extract', $1 || ':download')))))
      )
$$;

CREATE OR REPLACE FUNCTION doctrack_private.tracking(r public.tracking_trackingrecord, operation text)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT FROM doctrack_private.actor('tracking', operation) u
        WHERE CASE
          WHEN operation = 'create' THEN r.created_by_id = u.id AND r.status = 'DRAFT'
              AND (u.role = 'SYSTEM_ADMIN' OR r.originating_office_id = u.office_id)
          WHEN u.role = 'SYSTEM_ADMIN' THEN true
          WHEN r.status = 'DRAFT' THEN r.created_by_id = u.id AND (u.role <> 'ADMIN'
              OR u.office_id IN (r.originating_office_id, r.current_office_id)
              OR EXISTS (SELECT FROM public.tracking_routingstep s WHERE s.record_id=r.id
                         AND u.office_id IN (s.from_office_id,s.to_office_id)))
          WHEN operation IN ('view_record','view_content') THEN
              r.originating_office_id = u.office_id OR
              (u.role = 'ADMIN' AND r.current_office_id = u.office_id) OR
              EXISTS (SELECT FROM public.tracking_routingstep s WHERE s.record_id = r.id
                      AND u.office_id IN (s.from_office_id,s.to_office_id)) OR
              (u.role <> 'ADMIN' AND (r.created_by_id = u.id OR
                EXISTS (SELECT FROM public.tracking_recordaccessgrant g WHERE g.record_id = r.id
                        AND (g.user_id = u.id OR g.office_id = u.office_id))))
          WHEN operation = 'share' THEN r.created_by_id = u.id OR r.originating_office_id = u.office_id
              OR EXISTS (SELECT FROM public.tracking_routingstep s WHERE s.record_id = r.id
                         AND s.batch = r.current_batch AND s.to_office_id = u.office_id AND s.received_at IS NOT NULL)
          WHEN r.status = 'COMPLETED_PENDING_UPLOAD' THEN
              (u.role = 'ADMIN' AND u.office_id IN (r.current_office_id,r.originating_office_id))
              OR r.completed_by_id = u.id OR r.current_office_id = u.office_id
          ELSE r.status <> 'COMPLETED' AND EXISTS (
              SELECT FROM public.tracking_routingstep s WHERE s.record_id = r.id
                AND s.batch = r.current_batch AND s.to_office_id = u.office_id
                AND (operation = 'lock' OR s.received_at IS NOT NULL))
          END
    )
$$;

CREATE OR REPLACE FUNCTION doctrack_private.external_record(r public.tracking_externaltrackingrecord, operation text)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT FROM doctrack_private.actor('external_tracking', operation) u
        WHERE CASE
          WHEN operation = 'create' THEN u.role IN ('ADMIN','SYSTEM_ADMIN') AND r.created_by_id = u.id
              AND r.status = 'DRAFT' AND (u.role = 'SYSTEM_ADMIN' OR r.managing_office_id = u.office_id)
          WHEN u.role = 'SYSTEM_ADMIN' THEN true
          WHEN operation IN ('view_record','view_content') THEN
              (r.status <> 'DRAFT' OR u.role = 'ADMIN') AND (
                r.managing_office_id = u.office_id OR EXISTS (
                    SELECT FROM public.documents_document d WHERE d.external_tracking_record_id = r.id
                    AND d.office_id = u.office_id) OR (r.status <> 'DRAFT' AND (
                EXISTS (SELECT FROM public.tracking_externalroutingstep s WHERE s.record_id = r.id
                        AND u.office_id IN (s.from_office_id,s.to_office_id)) OR
                (u.role <> 'ADMIN' AND EXISTS (SELECT FROM public.tracking_externalrecordaccessgrant g
                      WHERE g.record_id = r.id AND (g.user_id = u.id OR g.office_id = u.office_id))))))
          WHEN operation = 'write' AND EXISTS (
              SELECT FROM (SELECT s.received_at FROM public.tracking_externalroutingstep s
                WHERE s.record_id = r.id ORDER BY s.sequence DESC LIMIT 1) latest
              WHERE latest.received_at IS NULL) THEN false
          ELSE (u.role = 'ADMIN' AND u.office_id = coalesce(
                 (SELECT d.office_id FROM public.documents_document d WHERE d.external_tracking_record_id = r.id),
                 r.current_office_id, (SELECT s.to_office_id FROM public.tracking_externalroutingstep s
                 WHERE s.record_id = r.id AND s.received_at IS NOT NULL AND s.to_office_id IS NOT NULL
                 ORDER BY s.sequence DESC LIMIT 1), r.managing_office_id))
              OR (operation <> 'share' AND r.status NOT IN ('DRAFT','COMPLETED','COMPLETED_PENDING_UPLOAD') AND (
                  r.current_office_id = u.office_id OR u.office_id = (
                    SELECT s.to_office_id FROM public.tracking_externalroutingstep s WHERE s.record_id = r.id
                    ORDER BY s.sequence DESC LIMIT 1)))
          END
    )
$$;

CREATE OR REPLACE FUNCTION doctrack_private.repository(d public.documents_document, operation text)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT FROM doctrack_private.actor(CASE d.collection WHEN 'OVPA' THEN 'repository'
                WHEN 'EXTERNAL' THEN 'external_repository' END, operation) u
        WHERE d.collection IN ('OVPA','EXTERNAL') AND CASE
          WHEN operation = 'create' THEN d.uploaded_by_id = u.id
              AND (u.role = 'SYSTEM_ADMIN' OR d.office_id = u.office_id)
              AND (d.collection = 'OVPA' OR u.role IN ('ADMIN','SYSTEM_ADMIN'))
          WHEN u.role = 'SYSTEM_ADMIN' THEN true
          WHEN operation = 'share' THEN u.role = 'ADMIN' AND d.is_active AND d.office_id = u.office_id
          WHEN operation NOT IN ('view_record','view_content') THEN d.office_id = u.office_id
              AND (d.collection = 'OVPA' OR u.role = 'ADMIN')
          ELSE (u.role <> 'ADMIN' OR d.office_id = u.office_id) AND (
            d.uploaded_by_id = u.id OR EXISTS (SELECT FROM public.documents_documentaccessgrant g
              WHERE g.document_id = d.id AND (g.user_id = u.id OR g.office_id = u.office_id))
            OR d.access_level = 'OVPA' OR (d.access_level <> 'RESTRICTED' AND (
              d.office_id = u.office_id OR EXISTS (SELECT FROM public.tracking_trackingrecord r
                  WHERE r.id = d.tracking_record_id AND r.originating_office_id = u.office_id)
              OR EXISTS (SELECT FROM public.tracking_routingstep s WHERE s.record_id = d.tracking_record_id
                  AND s.to_office_id = u.office_id)
              OR EXISTS (SELECT FROM public.tracking_externalroutingstep s WHERE s.record_id = d.external_tracking_record_id
                  AND u.office_id IN (s.from_office_id,s.to_office_id)))))
          END
    )
$$;
-- Filing can span two offices. Only the current, explicitly locked source
-- case can expose its newly created copy to the approving office in this transaction.
CREATE OR REPLACE FUNCTION doctrack_private.filing(d public.documents_document)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT d.uploaded_by_id = nullif(current_setting('doctrack.actor_id', true),'')::bigint
      AND d.source = 'DTS' AND d.collection = 'OVPA' AND EXISTS (
        SELECT FROM public.tracking_trackingrecord r, doctrack_private.actor('tracking','file') u
        WHERE r.id = d.tracking_record_id AND d.office_id = r.originating_office_id
          AND current_setting('doctrack.locked_row', true) = 'tracking_trackingrecord:' || r.id
          AND r.status IN ('COMPLETED_PENDING_UPLOAD','COMPLETED')
          AND (u.role = 'SYSTEM_ADMIN' OR (u.role = 'ADMIN'
               AND u.office_id IN (r.originating_office_id,r.current_office_id))))
$$;

CREATE OR REPLACE FUNCTION sp_etl_load_dw(
    p_mode  VARCHAR(20),
    p_date  DATE DEFAULT NULL
)
RETURNS TEXT
LANGUAGE plpgsql
AS $$
BEGIN

    -- =========================================================================
    -- 0. TRUNCATE KHI MODE = 'all'
    -- =========================================================================
    IF p_mode = 'all' THEN
        SET session_replication_role = 'replica';
        TRUNCATE TABLE
            bridge_jobrequire,
            fact_jobpostings,
            fact_pipeline_snapshot,
            fact_error_detail
        RESTART IDENTITY;
        SET session_replication_role = 'origin';
    END IF;

    -- =========================================================================
    -- 1. DIM_NGUON
    -- =========================================================================
    INSERT INTO dim_nguon (ten_nguon)
    SELECT DISTINCT website_clean
    FROM   fact_jobs_etl
    WHERE  website_clean IS NOT NULL
      AND  website_clean <> ''
    ON CONFLICT (ten_nguon) DO NOTHING;

    -- =========================================================================
    -- 2. DIM_CAPBAC
    -- =========================================================================
    INSERT INTO dim_capbac (ten_cap_bac)
    SELECT DISTINCT level_clean
    FROM   fact_jobs_etl
    WHERE  level_clean IS NOT NULL
      AND  level_clean <> ''
    ON CONFLICT (ten_cap_bac) DO NOTHING;

    -- =========================================================================
    -- 3. DIM_HINHTHUC
    -- =========================================================================
    INSERT INTO dim_hinhthuc (job_type, work_mode)
    SELECT DISTINCT
        COALESCE(NULLIF(job_type_clean,  ''), 'Full-time'),
        COALESCE(NULLIF(work_mode_clean, ''), 'Onsite')
    FROM fact_jobs_etl
    ON CONFLICT (job_type, work_mode) DO NOTHING;

    -- =========================================================================
    -- 4. DIM_NGANH
    -- =========================================================================
    INSERT INTO dim_nganh (cap_do_1, cap_do_2)
    SELECT DISTINCT
        industry_level1,
        industry_level2
    FROM  fact_jobs_etl
    WHERE industry_level1 IS NOT NULL
      AND industry_level1 <> ''
    ON CONFLICT (cap_do_1, cap_do_2) DO NOTHING;

    -- =========================================================================
    -- 5. DIM_DIADIEM
    -- =========================================================================
    INSERT INTO dim_diadiem (tinh_thanh, vung, is_vn)
    SELECT DISTINCT
        location_province,
        location_region,
        is_vn
    FROM  fact_jobs_etl
    WHERE location_province IS NOT NULL
      AND location_province <> ''
    ON CONFLICT (tinh_thanh) DO NOTHING;

    -- =========================================================================
    -- 6. DIM_CONGTY
    -- =========================================================================
    INSERT INTO dim_congty (ten_cong_ty, canonical_key, company_type, quy_mo_min, quy_mo_max)
    SELECT DISTINCT ON (ten_cong_ty)
        COALESCE(NULLIF(TRIM(company_name_clean), ''), 'Unknown') AS ten_cong_ty,
        company_canonical_key,
        company_type,
        company_size_min,
        company_size_max
    FROM  fact_jobs_etl
    WHERE is_duplicate = FALSE
    ORDER BY ten_cong_ty, etl_id DESC
    ON CONFLICT (ten_cong_ty) DO UPDATE SET
        canonical_key = COALESCE(EXCLUDED.canonical_key, dim_congty.canonical_key),
        company_type  = COALESCE(EXCLUDED.company_type,  dim_congty.company_type),
        quy_mo_min    = COALESCE(EXCLUDED.quy_mo_min,    dim_congty.quy_mo_min),
        quy_mo_max    = COALESCE(EXCLUDED.quy_mo_max,    dim_congty.quy_mo_max);

    -- =========================================================================
    -- 7. DIM_DANHMUCCONGVIEC
    -- =========================================================================
    INSERT INTO dim_danhmuccongviec (ten_danh_muc)
    SELECT DISTINCT job_category_clean
    FROM   fact_jobs_etl
    WHERE  job_category_clean IS NOT NULL
      AND  job_category_clean <> ''
    ON CONFLICT (ten_danh_muc) DO NOTHING;

    -- =========================================================================
    -- 8. FACT_JOBPOSTINGS
    -- =========================================================================
    INSERT INTO fact_jobpostings (
        etl_id, job_url, ngay_dang,
        dia_diem_id, cong_ty_id, nganh_id,
        cap_bac_id, hinh_thuc_id, nguon_id, danh_muc_id,
        is_it, is_duplicate, duplicate_of_id, dedup_method,
        job_title_clean, job_title_detect,
        so_luong_tuyen,
        salary_min, salary_max, salary_avg,
        salary_currency, conversion_rate,
        is_negotiable, salary_type,
        exp_min_yr, exp_max_yr, is_exp_required,
        etl_run_id, ngay_crawl, ngay_deadline
    )
    SELECT
        src.etl_id,
        src.job_url,
        src.job_posted_at_clean,
        dd.dia_diem_id,
        ct.cong_ty_id,
        ng.nganh_id,
        cb.cap_bac_id,
        ht.hinh_thuc_id,
        ns.nguon_id,
        dm.danh_muc_id,
        src.is_it,
        src.is_duplicate,
        src.duplicate_of_id,
        src.dedup_method,
        src.job_title_clean,
        src.job_title_detect,
        COALESCE(src.number_recruit_clean, 1),
        src.salary_min,
        src.salary_max,
        CASE
            WHEN src.salary_min IS NOT NULL AND src.salary_max IS NOT NULL
                THEN ROUND((src.salary_min + src.salary_max) / 2.0)
            WHEN src.salary_min IS NOT NULL THEN src.salary_min
            WHEN src.salary_max IS NOT NULL THEN src.salary_max
            ELSE NULL
        END,
        src.salary_currency,
        src.conversion_rate,
        src.is_negotiable,
        src.salary_type,
        src.exp_min_yr,
        src.exp_max_yr,
        src.is_exp_required,
        src.etl_run_id,
        CASE
            WHEN src.scraped_at IS NOT NULL AND src.scraped_at <> ''
            THEN src.scraped_at::date
            ELSE NULL
        END,
        src.job_deadline_clean

    FROM fact_jobs_etl src

    LEFT JOIN dim_diadiem dd
           ON src.location_province = dd.tinh_thanh
    LEFT JOIN dim_congty ct
           ON COALESCE(NULLIF(TRIM(src.company_name_clean), ''), 'Unknown') = ct.ten_cong_ty
    LEFT JOIN dim_nganh ng
           ON src.industry_level1 = ng.cap_do_1
          AND src.industry_level2 = ng.cap_do_2
    LEFT JOIN dim_capbac cb
           ON src.level_clean = cb.ten_cap_bac
    LEFT JOIN dim_hinhthuc ht
           ON COALESCE(NULLIF(src.job_type_clean,  ''), 'Full-time') = ht.job_type
          AND COALESCE(NULLIF(src.work_mode_clean, ''), 'Onsite')    = ht.work_mode
    LEFT JOIN dim_nguon ns
           ON src.website_clean = ns.ten_nguon
    LEFT JOIN dim_danhmuccongviec dm
           ON src.job_category_clean = dm.ten_danh_muc

    WHERE src.is_valid = TRUE
      AND (
               p_mode = 'all'
            OR (p_mode = 'today' AND src.etl_processed_at::date = CURRENT_DATE)
            OR (p_mode = 'date'  AND src.etl_processed_at::date = p_date)
          )

    ON CONFLICT (etl_id) DO UPDATE SET
        is_it            = EXCLUDED.is_it,
        is_duplicate     = EXCLUDED.is_duplicate,
        duplicate_of_id  = EXCLUDED.duplicate_of_id,
        dedup_method     = EXCLUDED.dedup_method,
        ngay_dang        = EXCLUDED.ngay_dang,
        job_title_clean  = EXCLUDED.job_title_clean,
        job_title_detect = EXCLUDED.job_title_detect,
        so_luong_tuyen   = EXCLUDED.so_luong_tuyen,
        salary_min       = EXCLUDED.salary_min,
        salary_max       = EXCLUDED.salary_max,
        salary_avg       = EXCLUDED.salary_avg,
        salary_currency  = EXCLUDED.salary_currency,
        conversion_rate  = EXCLUDED.conversion_rate,
        is_negotiable    = EXCLUDED.is_negotiable,
        salary_type      = EXCLUDED.salary_type,
        exp_min_yr       = EXCLUDED.exp_min_yr,
        exp_max_yr       = EXCLUDED.exp_max_yr,
        is_exp_required  = EXCLUDED.is_exp_required,
        ngay_crawl       = EXCLUDED.ngay_crawl,
        updated_at       = CURRENT_TIMESTAMP;

    -- =========================================================================
    -- 9. DIM_REQUIRE
    -- =========================================================================
    INSERT INTO dim_require (require_type, require_value)
    SELECT DISTINCT 'hard_skill', TRIM(val)
    FROM  fact_jobs_etl
    CROSS JOIN LATERAL unnest(string_to_array(
        regexp_replace(hard_skills, '\s*,\s*', ',', 'g'), ',')) AS val
    WHERE hard_skills IS NOT NULL AND is_duplicate = FALSE AND TRIM(val) <> ''

    UNION ALL

    SELECT DISTINCT 'soft_skill', TRIM(val)
    FROM  fact_jobs_etl
    CROSS JOIN LATERAL unnest(string_to_array(
        regexp_replace(soft_skills, '\s*,\s*', ',', 'g'), ',')) AS val
    WHERE soft_skills IS NOT NULL AND is_duplicate = FALSE AND TRIM(val) <> ''

    UNION ALL

    SELECT DISTINCT 'certification', TRIM(val)
    FROM  fact_jobs_etl
    CROSS JOIN LATERAL unnest(string_to_array(
        regexp_replace(certifications, '\s*,\s*', ',', 'g'), ',')) AS val
    WHERE certifications IS NOT NULL AND is_duplicate = FALSE AND TRIM(val) <> ''

    UNION ALL

    SELECT DISTINCT 'language', TRIM(val)
    FROM  fact_jobs_etl
    CROSS JOIN LATERAL unnest(string_to_array(
        regexp_replace(languages, '\s*,\s*', ',', 'g'), ',')) AS val
    WHERE languages IS NOT NULL AND is_duplicate = FALSE AND TRIM(val) <> ''

    UNION ALL

    SELECT DISTINCT 'major', TRIM(val)
    FROM  fact_jobs_etl
    CROSS JOIN LATERAL unnest(string_to_array(
        regexp_replace(major, '\s*,\s*', ',', 'g'), ',')) AS val
    WHERE major IS NOT NULL AND is_duplicate = FALSE AND TRIM(val) <> ''

    ON CONFLICT (require_type, require_value) DO NOTHING;

    -- =========================================================================
    -- 10. BRIDGE_JOBREQUIRE
    -- =========================================================================
    INSERT INTO bridge_jobrequire (fact_id, require_id)

    SELECT f.posting_id, r.require_id
    FROM  fact_jobs_etl src
    JOIN  fact_jobpostings f ON src.etl_id = f.etl_id
    CROSS JOIN LATERAL unnest(string_to_array(
        regexp_replace(src.hard_skills, '\s*,\s*', ',', 'g'), ',')) AS val
    JOIN  dim_require r ON r.require_type = 'hard_skill' AND r.require_value = TRIM(val)
    WHERE src.hard_skills IS NOT NULL AND src.is_duplicate = FALSE AND TRIM(val) <> ''
      AND (p_mode = 'all'
        OR (p_mode = 'today' AND src.etl_processed_at::date = CURRENT_DATE)
        OR (p_mode = 'date'  AND src.etl_processed_at::date = p_date))

    UNION ALL

    SELECT f.posting_id, r.require_id
    FROM  fact_jobs_etl src
    JOIN  fact_jobpostings f ON src.etl_id = f.etl_id
    CROSS JOIN LATERAL unnest(string_to_array(
        regexp_replace(src.soft_skills, '\s*,\s*', ',', 'g'), ',')) AS val
    JOIN  dim_require r ON r.require_type = 'soft_skill' AND r.require_value = TRIM(val)
    WHERE src.soft_skills IS NOT NULL AND src.is_duplicate = FALSE AND TRIM(val) <> ''
      AND (p_mode = 'all'
        OR (p_mode = 'today' AND src.etl_processed_at::date = CURRENT_DATE)
        OR (p_mode = 'date'  AND src.etl_processed_at::date = p_date))

    UNION ALL

    SELECT f.posting_id, r.require_id
    FROM  fact_jobs_etl src
    JOIN  fact_jobpostings f ON src.etl_id = f.etl_id
    CROSS JOIN LATERAL unnest(string_to_array(
        regexp_replace(src.certifications, '\s*,\s*', ',', 'g'), ',')) AS val
    JOIN  dim_require r ON r.require_type = 'certification' AND r.require_value = TRIM(val)
    WHERE src.certifications IS NOT NULL AND src.is_duplicate = FALSE AND TRIM(val) <> ''
      AND (p_mode = 'all'
        OR (p_mode = 'today' AND src.etl_processed_at::date = CURRENT_DATE)
        OR (p_mode = 'date'  AND src.etl_processed_at::date = p_date))

    UNION ALL

    SELECT f.posting_id, r.require_id
    FROM  fact_jobs_etl src
    JOIN  fact_jobpostings f ON src.etl_id = f.etl_id
    CROSS JOIN LATERAL unnest(string_to_array(
        regexp_replace(src.languages, '\s*,\s*', ',', 'g'), ',')) AS val
    JOIN  dim_require r ON r.require_type = 'language' AND r.require_value = TRIM(val)
    WHERE src.languages IS NOT NULL AND src.is_duplicate = FALSE AND TRIM(val) <> ''
      AND (p_mode = 'all'
        OR (p_mode = 'today' AND src.etl_processed_at::date = CURRENT_DATE)
        OR (p_mode = 'date'  AND src.etl_processed_at::date = p_date))

    UNION ALL

    SELECT f.posting_id, r.require_id
    FROM  fact_jobs_etl src
    JOIN  fact_jobpostings f ON src.etl_id = f.etl_id
    CROSS JOIN LATERAL unnest(string_to_array(
        regexp_replace(src.major, '\s*,\s*', ',', 'g'), ',')) AS val
    JOIN  dim_require r ON r.require_type = 'major' AND r.require_value = TRIM(val)
    WHERE src.major IS NOT NULL AND src.is_duplicate = FALSE AND TRIM(val) <> ''
      AND (p_mode = 'all'
        OR (p_mode = 'today' AND src.etl_processed_at::date = CURRENT_DATE)
        OR (p_mode = 'date'  AND src.etl_processed_at::date = p_date))

    ON CONFLICT (fact_id, require_id) DO NOTHING;

    -- =========================================================================
    -- 11. VIEWS
    -- =========================================================================
    EXECUTE 'CREATE OR REPLACE VIEW vw_jobpostings_unique AS
             SELECT * FROM fact_jobpostings WHERE is_duplicate = FALSE';

    EXECUTE 'CREATE OR REPLACE VIEW vw_jobpostings_all AS
             SELECT * FROM fact_jobpostings';

    RETURN 'ETL DW hoàn tất — mode=' || p_mode ||
           COALESCE(' date=' || p_date::text, '');

END;
$$;
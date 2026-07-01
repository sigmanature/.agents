// SPDX-License-Identifier: GPL-2.0
#include <linux/debugfs.h>
#include <linux/hash.h>
#include <linux/init.h>
#include <linux/jiffies.h>
#include <linux/mthp_experiment.h>
#include <linux/page-flags.h>
#include <linux/proc_fs.h>
#include <linux/rcupdate.h>
#include <linux/seq_file.h>
#include <linux/spinlock.h>
#include <linux/string.h>
#include <linux/swapops.h>

atomic64_t mthp_exp_counters[MTHP_EXP_NR_COUNTERS];

#define MTHP_EXP_FOLIO_BITS	15
#define MTHP_EXP_SWAP_BITS	16
#define MTHP_EXP_FOLIO_SLOTS	(1U << MTHP_EXP_FOLIO_BITS)
#define MTHP_EXP_SWAP_SLOTS	(1U << MTHP_EXP_SWAP_BITS)

struct mthp_exp_folio_origin_record {
	spinlock_t lock;
	bool valid;
	unsigned long pfn;
	unsigned long mm;
	unsigned long addr;
	unsigned long jiffies;
	unsigned int origin;
	unsigned int subreason;
	pid_t pid;
	pid_t tgid;
	char comm[TASK_COMM_LEN];
	char leader_comm[TASK_COMM_LEN];
	char vma_name[MTHP_EXP_NAME_LEN];
};

struct mthp_exp_swap_slot {
	spinlock_t lock;
	bool valid;
	unsigned long key;
	unsigned long seq;
	struct mthp_exp_swap_birth_record rec;
};

static struct mthp_exp_folio_origin_record mthp_exp_folio_origins[MTHP_EXP_FOLIO_SLOTS];
static struct mthp_exp_swap_slot mthp_exp_swap_births[MTHP_EXP_SWAP_SLOTS];
static atomic64_t mthp_exp_birth_seq;

static unsigned long mthp_exp_swap_key(swp_entry_t entry)
{
	return swp_offset(entry) ^ ((unsigned long)swp_type(entry) << 56);
}

static unsigned int mthp_exp_folio_idx(unsigned long pfn)
{
	return hash_long(pfn, MTHP_EXP_FOLIO_BITS);
}

static unsigned int mthp_exp_swap_idx(unsigned long key)
{
	return hash_long(key, MTHP_EXP_SWAP_BITS);
}

static void mthp_exp_copy_task(char *comm, char *leader_comm)
{
	memcpy(comm, current->comm, TASK_COMM_LEN);
	memcpy(leader_comm,
	       current->group_leader ? current->group_leader->comm :
	       current->comm, TASK_COMM_LEN);
}

static void mthp_exp_copy_vma_name(char *dst, struct vm_area_struct *vma)
{
	struct anon_vma_name *name;

	dst[0] = '\0';
	if (!vma)
		return;
	name = anon_vma_name(vma);
	if (!name)
		return;
	strscpy(dst, name->name, MTHP_EXP_NAME_LEN);
}

static bool mthp_exp_lookup_folio_origin(
		struct folio *folio, struct mthp_exp_folio_origin_record *out)
{
	struct mthp_exp_folio_origin_record *slot;
	unsigned long pfn = folio_pfn(folio);
	unsigned int idx = mthp_exp_folio_idx(pfn);
	unsigned long flags;
	bool found = false;

	slot = &mthp_exp_folio_origins[idx];
	spin_lock_irqsave(&slot->lock, flags);
	if (slot->valid && slot->pfn == pfn) {
		*out = *slot;
		found = true;
	}
	spin_unlock_irqrestore(&slot->lock, flags);
	return found;
}

static const char * const mthp_exp_counter_names[MTHP_EXP_NR_COUNTERS] = {
	[MTHP_EXP_UFFD_MFILL_COPY_TOTAL] = "uffd_mfill_copy_total",
	[MTHP_EXP_UFFD_MFILL_COPY_ORDER0] = "uffd_mfill_copy_order0",
	[MTHP_EXP_UFFD_MFILL_ZEROPAGE_TOTAL] = "uffd_mfill_zeropage_total",
	[MTHP_EXP_UFFD_MFILL_ZEROPAGE_ZERO_PFN] = "uffd_mfill_zeropage_zero_pfn",
	[MTHP_EXP_UFFD_MFILL_ZEROPAGE_ZEROED_FOLIO] = "uffd_mfill_zeropage_zeroed_folio",
	[MTHP_EXP_UFFD_MFILL_SHMEM_COPY_TOTAL] = "uffd_mfill_shmem_copy_total",
	[MTHP_EXP_UFFD_MFILL_SHMEM_ZEROPAGE_TOTAL] = "uffd_mfill_shmem_zeropage_total",
	[MTHP_EXP_UFFD_MFILL_CONTINUE_TOTAL] = "uffd_mfill_continue_total",
	[MTHP_EXP_SWPIN_SYNCHRONOUS_PATH] = "swpin_synchronous_path",
	[MTHP_EXP_SWPIN_READAHEAD_PATH] = "swpin_readahead_path",
	[MTHP_EXP_SWPIN_SWAPCACHE_HIT] = "swpin_swapcache_hit",
	[MTHP_EXP_SWPIN_SYNC_FALLBACK_UFFD] = "swpin_sync_fallback_uffd",
	[MTHP_EXP_SWPIN_SYNC_FALLBACK_ZSWAP_GUARD] = "swpin_sync_fallback_zswap_guard",
	[MTHP_EXP_SWPIN_SYNC_FALLBACK_NO_ORDERS] = "swpin_sync_fallback_no_orders",
	[MTHP_EXP_SWPIN_SYNC_FALLBACK_NO_ORDERS_ALLOWABLE] = "swpin_sync_fallback_no_orders_allowable",
	[MTHP_EXP_SWPIN_SYNC_FALLBACK_NO_ORDERS_VMA_SUITABLE] = "swpin_sync_fallback_no_orders_vma_suitable",
	[MTHP_EXP_SWPIN_SYNC_FALLBACK_NO_ORDERS_SWAP_SUITABLE] = "swpin_sync_fallback_no_orders_swap_suitable",
	[MTHP_EXP_SWPIN_SYNC_NO_ORDERS_ALLOWABLE_ANON_MASK] = "swpin_sync_no_orders_allowable_anon_mask",
	[MTHP_EXP_SWPIN_SYNC_NO_ORDERS_ALLOWABLE_VMA_NOHUGEPAGE] = "swpin_sync_no_orders_allowable_vma_nohugepage",
	[MTHP_EXP_SWPIN_SYNC_NO_ORDERS_ALLOWABLE_MM_DISABLE_THP] = "swpin_sync_no_orders_allowable_mm_disable_thp",
	[MTHP_EXP_SWPIN_SYNC_NO_ORDERS_ALLOWABLE_MM_EXCEPT_ADVISED] = "swpin_sync_no_orders_allowable_mm_except_advised",
	[MTHP_EXP_SWPIN_SYNC_NO_ORDERS_ALLOWABLE_UNSUPPORTED_ORDERS] = "swpin_sync_no_orders_allowable_unsupported_orders",
	[MTHP_EXP_SWPIN_SYNC_NO_ORDERS_ALLOWABLE_NONANON_GLOBAL_DISABLED] = "swpin_sync_no_orders_allowable_nonanon_global_disabled",
	[MTHP_EXP_SWPIN_SYNC_NO_ORDERS_ALLOWABLE_NONANON_NO_HUGE_FAULT] = "swpin_sync_no_orders_allowable_nonanon_no_huge_fault",
	[MTHP_EXP_SWPIN_SYNC_NO_ORDERS_ALLOWABLE_VDSO] = "swpin_sync_no_orders_allowable_vdso",
	[MTHP_EXP_SWPIN_SYNC_NO_ORDERS_ALLOWABLE_HW_DISABLED] = "swpin_sync_no_orders_allowable_hw_disabled",
	[MTHP_EXP_SWPIN_SYNC_NO_ORDERS_ALLOWABLE_UNKNOWN] = "swpin_sync_no_orders_allowable_unknown",
	[MTHP_EXP_SWPIN_SYNC_FALLBACK_PTE_MAP] = "swpin_sync_fallback_pte_map",
	[MTHP_EXP_SWPIN_SYNC_FALLBACK_CAN_SWAPIN_THP] = "swpin_sync_fallback_can_swapin_thp",
	[MTHP_EXP_SWPIN_SYNC_ORDER0_FALLBACK] = "swpin_sync_order0_fallback",
	[MTHP_EXP_SWPIN_READ_SWAP_CACHE_ASYNC_ORDER0_ALLOC] = "swpin_read_swap_cache_async_order0_alloc",
	[MTHP_EXP_UFFD_MFILL_SHMEM_COPY_ACCT_FAIL] = "uffd_mfill_shmem_copy_acct_fail",
	[MTHP_EXP_UFFD_MFILL_SHMEM_COPY_ALLOC_FAIL] = "uffd_mfill_shmem_copy_alloc_fail",
	[MTHP_EXP_UFFD_MFILL_SHMEM_COPY_USER_RETRY] = "uffd_mfill_shmem_copy_user_retry",
	[MTHP_EXP_UFFD_MFILL_SHMEM_COPY_EOF_FAIL] = "uffd_mfill_shmem_copy_eof_fail",
	[MTHP_EXP_UFFD_MFILL_SHMEM_COPY_CHARGE_FAIL] = "uffd_mfill_shmem_copy_charge_fail",
	[MTHP_EXP_UFFD_MFILL_SHMEM_COPY_PAGECACHE_FAIL] = "uffd_mfill_shmem_copy_pagecache_fail",
	[MTHP_EXP_UFFD_MFILL_SHMEM_COPY_PTE_INSTALL_FAIL] = "uffd_mfill_shmem_copy_pte_install_fail",
	[MTHP_EXP_UFFD_MFILL_SHMEM_COPY_SUCCESS] = "uffd_mfill_shmem_copy_success",
	[MTHP_EXP_UFFD_MFILL_SHMEM_ZEROPAGE_ACCT_FAIL] = "uffd_mfill_shmem_zeropage_acct_fail",
	[MTHP_EXP_UFFD_MFILL_SHMEM_ZEROPAGE_ALLOC_FAIL] = "uffd_mfill_shmem_zeropage_alloc_fail",
	[MTHP_EXP_UFFD_MFILL_SHMEM_ZEROPAGE_EOF_FAIL] = "uffd_mfill_shmem_zeropage_eof_fail",
	[MTHP_EXP_UFFD_MFILL_SHMEM_ZEROPAGE_CHARGE_FAIL] = "uffd_mfill_shmem_zeropage_charge_fail",
	[MTHP_EXP_UFFD_MFILL_SHMEM_ZEROPAGE_PAGECACHE_FAIL] = "uffd_mfill_shmem_zeropage_pagecache_fail",
	[MTHP_EXP_UFFD_MFILL_SHMEM_ZEROPAGE_PTE_INSTALL_FAIL] = "uffd_mfill_shmem_zeropage_pte_install_fail",
	[MTHP_EXP_UFFD_MFILL_SHMEM_ZEROPAGE_SUCCESS] = "uffd_mfill_shmem_zeropage_success",
	[MTHP_EXP_ANON_FAULT_SINGLE_PAGE_FALLBACK] = "anon_fault_single_page_fallback",
	[MTHP_EXP_ANON_FAULT_FALLBACK_UFFD_ARMED] = "anon_fault_fallback_uffd_armed",
	[MTHP_EXP_ANON_FAULT_FALLBACK_NO_ORDERS_ALLOWABLE] = "anon_fault_fallback_no_orders_allowable",
	[MTHP_EXP_ANON_FAULT_FALLBACK_NO_ORDERS_SUITABLE] = "anon_fault_fallback_no_orders_suitable",
	[MTHP_EXP_ANON_FAULT_FALLBACK_PTE_RANGE_OCCUPIED] = "anon_fault_fallback_pte_range_occupied",
	[MTHP_EXP_ANON_FAULT_FALLBACK_ALLOC_FAIL] = "anon_fault_fallback_alloc_fail",
	[MTHP_EXP_ANON_FAULT_FALLBACK_CHARGE_FAIL] = "anon_fault_fallback_charge_fail",
	[MTHP_EXP_ANON_FAULT_FALLBACK_ALL_ORDERS_FAILED] = "anon_fault_fallback_all_orders_failed",
	[MTHP_EXP_ANON_ALLOWABLE_REQUESTED_LACKS_ORDER2] = "anon_allowable_requested_lacks_order2",
	[MTHP_EXP_ANON_ALLOWABLE_SUPPORTED_LACKS_ORDER2] = "anon_allowable_supported_lacks_order2",
	[MTHP_EXP_ANON_ALLOWABLE_ORDER2_NOT_IN_ALWAYS] = "anon_allowable_order2_not_in_always",
	[MTHP_EXP_ANON_ALLOWABLE_ORDER2_ONLY_IN_MADVISE_BUT_VMA_NOT_HUGE] = "anon_allowable_order2_only_in_madvise_but_vma_not_huge",
	[MTHP_EXP_ANON_ALLOWABLE_ORDER2_ONLY_IN_INHERIT_BUT_GLOBAL_NOT_ALLOWING] = "anon_allowable_order2_only_in_inherit_but_global_not_allowing",
	[MTHP_EXP_ANON_ALLOWABLE_EFFECTIVE_MASK_LACKS_ORDER2] = "anon_allowable_effective_mask_lacks_order2",
	[MTHP_EXP_ANON_ALLOWABLE_EFFECTIVE_MASK_NO_INTERSECTION] = "anon_allowable_effective_mask_no_intersection",
	[MTHP_EXP_ANON_ALLOWABLE_UNEXPECTED_DISABLED] = "anon_allowable_unexpected_disabled",
	[MTHP_EXP_ANON_ALLOWABLE_TEMPORARY_STACK] = "anon_allowable_temporary_stack",
	[MTHP_EXP_ANON_ALLOWABLE_UNKNOWN] = "anon_allowable_unknown",
	[MTHP_EXP_ANON_SUITABLE_ORDER2_VMA_TOO_SMALL] = "anon_suitable_order2_vma_too_small",
	[MTHP_EXP_ANON_SUITABLE_ORDER2_LEFT_BOUNDARY] = "anon_suitable_order2_left_boundary",
	[MTHP_EXP_ANON_SUITABLE_ORDER2_RIGHT_BOUNDARY] = "anon_suitable_order2_right_boundary",
	[MTHP_EXP_ANON_SUITABLE_UNKNOWN] = "anon_suitable_unknown",
	[MTHP_EXP_SWPIN_ALLOWABLE_REQUESTED_LACKS_ORDER2] = "swpin_allowable_requested_lacks_order2",
	[MTHP_EXP_SWPIN_ALLOWABLE_SUPPORTED_LACKS_ORDER2] = "swpin_allowable_supported_lacks_order2",
	[MTHP_EXP_SWPIN_ALLOWABLE_ORDER2_NOT_IN_ALWAYS] = "swpin_allowable_order2_not_in_always",
	[MTHP_EXP_SWPIN_ALLOWABLE_ORDER2_ONLY_IN_MADVISE_BUT_VMA_NOT_HUGE] = "swpin_allowable_order2_only_in_madvise_but_vma_not_huge",
	[MTHP_EXP_SWPIN_ALLOWABLE_ORDER2_ONLY_IN_INHERIT_BUT_GLOBAL_NOT_ALLOWING] = "swpin_allowable_order2_only_in_inherit_but_global_not_allowing",
	[MTHP_EXP_SWPIN_ALLOWABLE_EFFECTIVE_MASK_LACKS_ORDER2] = "swpin_allowable_effective_mask_lacks_order2",
	[MTHP_EXP_SWPIN_ALLOWABLE_EFFECTIVE_MASK_NO_INTERSECTION] = "swpin_allowable_effective_mask_no_intersection",
	[MTHP_EXP_SWPIN_ALLOWABLE_UNEXPECTED_DISABLED] = "swpin_allowable_unexpected_disabled",
	[MTHP_EXP_SWPIN_ALLOWABLE_TEMPORARY_STACK] = "swpin_allowable_temporary_stack",
	[MTHP_EXP_SWPIN_ALLOWABLE_NONANON_GLOBAL_DISABLED] = "swpin_allowable_nonanon_global_disabled",
	[MTHP_EXP_SWPIN_ALLOWABLE_NONANON_NO_HUGE_FAULT] = "swpin_allowable_nonanon_no_huge_fault",
	[MTHP_EXP_SWPIN_ALLOWABLE_UNKNOWN] = "swpin_allowable_unknown",
	[MTHP_EXP_SWPIN_SUITABLE_ORDER2_VMA_TOO_SMALL] = "swpin_suitable_order2_vma_too_small",
	[MTHP_EXP_SWPIN_SUITABLE_ORDER2_LEFT_BOUNDARY] = "swpin_suitable_order2_left_boundary",
	[MTHP_EXP_SWPIN_SUITABLE_ORDER2_RIGHT_BOUNDARY] = "swpin_suitable_order2_right_boundary",
	[MTHP_EXP_SWPIN_SUITABLE_FILE_PGOFF_MISALIGNED_ORDER2] = "swpin_suitable_file_pgoff_misaligned_order2",
	[MTHP_EXP_SWPIN_SUITABLE_UNKNOWN] = "swpin_suitable_unknown",
	[MTHP_EXP_SWPIN_SWAP_SUITABLE_ORDER2_OFFSET_MISMATCH] = "swpin_swap_suitable_order2_offset_mismatch",
	[MTHP_EXP_SWPIN_ORDER2_PHASE_PASS] = "swpin_order2_phase_pass",
	[MTHP_EXP_SWPIN_ORDER2_CAN_SWAPIN_PASS] = "swpin_order2_can_swapin_pass",
	[MTHP_EXP_SWPIN_ORDER2_ALLOC_SUCCESS] = "swpin_order2_alloc_success",
	[MTHP_EXP_SWPIN_ORDER2_ALLOC_FAIL] = "swpin_order2_alloc_fail",
	[MTHP_EXP_SWPIN_ORDER2_CHARGE_FAIL] = "swpin_order2_charge_fail",
	[MTHP_EXP_SWAPOUT_ORDER2_ALLOC_ATTEMPT] = "swapout_order2_alloc_attempt",
	[MTHP_EXP_SWAPOUT_ORDER2_ALLOC_SUCCESS] = "swapout_order2_alloc_success",
	[MTHP_EXP_SWAPOUT_ORDER2_ALLOC_FAIL] = "swapout_order2_alloc_fail",
	[MTHP_EXP_SWAPOUT_ORDER2_ALLOC_OFFSET_MOD0] = "swapout_order2_alloc_offset_mod0",
	[MTHP_EXP_SWAPOUT_ORDER2_ALLOC_OFFSET_MOD1] = "swapout_order2_alloc_offset_mod1",
	[MTHP_EXP_SWAPOUT_ORDER2_ALLOC_OFFSET_MOD2] = "swapout_order2_alloc_offset_mod2",
	[MTHP_EXP_SWAPOUT_ORDER2_ALLOC_OFFSET_MOD3] = "swapout_order2_alloc_offset_mod3",
	[MTHP_EXP_SWAPOUT_ORDER2_ALLOC_MEMCG_FAIL] = "swapout_order2_alloc_memcg_fail",
	[MTHP_EXP_SWAPOUT_LARGE_ALLOC_FAIL_SPLIT] = "swapout_large_alloc_fail_split",
	[MTHP_EXP_SWAPOUT_LARGE_ALLOC_FAIL_SPLIT_ORDER2] = "swapout_large_alloc_fail_split_order2",
	[MTHP_EXP_SWAPOUT_ORDER0_STEAL_HIGHER] = "swapout_order0_steal_higher",
	[MTHP_EXP_SWAPOUT_ORDER0_STEAL_FROM_ORDER1] = "swapout_order0_steal_from_order1",
	[MTHP_EXP_SWAPOUT_ORDER0_STEAL_FROM_ORDER2] = "swapout_order0_steal_from_order2",
	[MTHP_EXP_SWAPOUT_ORDER0_STEAL_FROM_ORDER3] = "swapout_order0_steal_from_order3",
	[MTHP_EXP_SWAPOUT_ORDER0_STEAL_FROM_ORDER4] = "swapout_order0_steal_from_order4",
	[MTHP_EXP_SWAPOUT_ORDER0_STEAL_FROM_ORDER5] = "swapout_order0_steal_from_order5",
	[MTHP_EXP_SWAPOUT_ORDER0_STEAL_FROM_ORDER6] = "swapout_order0_steal_from_order6",
	[MTHP_EXP_SWAPOUT_ORDER0_STEAL_FROM_ORDER7] = "swapout_order0_steal_from_order7",
	[MTHP_EXP_SWAPOUT_ORDER0_STEAL_FROM_ORDER8] = "swapout_order0_steal_from_order8",
	[MTHP_EXP_SWAPOUT_ORDER0_STEAL_FROM_ORDER9] = "swapout_order0_steal_from_order9",
	[MTHP_EXP_SWPIN_OFFSET_MISMATCH_ADDR0_SWP0] = "swpin_offset_mismatch_addr0_swp0",
	[MTHP_EXP_SWPIN_OFFSET_MISMATCH_ADDR0_SWP1] = "swpin_offset_mismatch_addr0_swp1",
	[MTHP_EXP_SWPIN_OFFSET_MISMATCH_ADDR0_SWP2] = "swpin_offset_mismatch_addr0_swp2",
	[MTHP_EXP_SWPIN_OFFSET_MISMATCH_ADDR0_SWP3] = "swpin_offset_mismatch_addr0_swp3",
	[MTHP_EXP_SWPIN_OFFSET_MISMATCH_ADDR1_SWP0] = "swpin_offset_mismatch_addr1_swp0",
	[MTHP_EXP_SWPIN_OFFSET_MISMATCH_ADDR1_SWP1] = "swpin_offset_mismatch_addr1_swp1",
	[MTHP_EXP_SWPIN_OFFSET_MISMATCH_ADDR1_SWP2] = "swpin_offset_mismatch_addr1_swp2",
	[MTHP_EXP_SWPIN_OFFSET_MISMATCH_ADDR1_SWP3] = "swpin_offset_mismatch_addr1_swp3",
	[MTHP_EXP_SWPIN_OFFSET_MISMATCH_ADDR2_SWP0] = "swpin_offset_mismatch_addr2_swp0",
	[MTHP_EXP_SWPIN_OFFSET_MISMATCH_ADDR2_SWP1] = "swpin_offset_mismatch_addr2_swp1",
	[MTHP_EXP_SWPIN_OFFSET_MISMATCH_ADDR2_SWP2] = "swpin_offset_mismatch_addr2_swp2",
	[MTHP_EXP_SWPIN_OFFSET_MISMATCH_ADDR2_SWP3] = "swpin_offset_mismatch_addr2_swp3",
	[MTHP_EXP_SWPIN_OFFSET_MISMATCH_ADDR3_SWP0] = "swpin_offset_mismatch_addr3_swp0",
	[MTHP_EXP_SWPIN_OFFSET_MISMATCH_ADDR3_SWP1] = "swpin_offset_mismatch_addr3_swp1",
	[MTHP_EXP_SWPIN_OFFSET_MISMATCH_ADDR3_SWP2] = "swpin_offset_mismatch_addr3_swp2",
	[MTHP_EXP_SWPIN_OFFSET_MISMATCH_ADDR3_SWP3] = "swpin_offset_mismatch_addr3_swp3",
	[MTHP_EXP_SWPIN_MISMATCH_BIRTH_FOUND] = "swpin_mismatch_birth_found",
	[MTHP_EXP_SWPIN_MISMATCH_BIRTH_NOT_FOUND] = "swpin_mismatch_birth_not_found",
	[MTHP_EXP_SWPIN_MISMATCH_BIRTH_STALE] = "swpin_mismatch_birth_stale",
	[MTHP_EXP_SWPIN_MISMATCH_BIRTH_ORDER0] = "swpin_mismatch_birth_order0",
	[MTHP_EXP_SWPIN_MISMATCH_BIRTH_ORDER2] = "swpin_mismatch_birth_order2",
	[MTHP_EXP_SWPIN_MISMATCH_BIRTH_OTHER_ORDER] = "swpin_mismatch_birth_other_order",
	[MTHP_EXP_SWPIN_MISMATCH_CASE_A_BIRTH_PHASE_BAD] = "swpin_mismatch_case_a_birth_phase_bad",
	[MTHP_EXP_SWPIN_MISMATCH_CASE_B_BIRTH_GOOD_NOW_BAD] = "swpin_mismatch_case_b_birth_good_now_bad",
	[MTHP_EXP_SWPIN_MISMATCH_CASE_C_ENTRY_ORDER0] = "swpin_mismatch_case_c_entry_order0",
	[MTHP_EXP_SWPIN_MISMATCH_ORDER0_ORIGIN_UFFD_COPY] = "swpin_mismatch_order0_origin_uffd_copy",
	[MTHP_EXP_SWPIN_MISMATCH_ORDER0_ORIGIN_UFFD_ZEROPAGE] = "swpin_mismatch_order0_origin_uffd_zeropage",
	[MTHP_EXP_SWPIN_MISMATCH_ORDER0_ORIGIN_COW] = "swpin_mismatch_order0_origin_cow",
	[MTHP_EXP_SWPIN_MISMATCH_ORDER0_ORIGIN_ANON_FALLBACK_UFFD] = "swpin_mismatch_order0_origin_anon_fallback_uffd",
	[MTHP_EXP_SWPIN_MISMATCH_ORDER0_ORIGIN_ANON_FALLBACK_ALLOWABLE] = "swpin_mismatch_order0_origin_anon_fallback_allowable",
	[MTHP_EXP_SWPIN_MISMATCH_ORDER0_ORIGIN_ANON_FALLBACK_VMA_SUITABLE] = "swpin_mismatch_order0_origin_anon_fallback_vma_suitable",
	[MTHP_EXP_SWPIN_MISMATCH_ORDER0_ORIGIN_ANON_FALLBACK_PTE_OCCUPIED] = "swpin_mismatch_order0_origin_anon_fallback_pte_occupied",
	[MTHP_EXP_SWPIN_MISMATCH_ORDER0_ORIGIN_ANON_FALLBACK_ALLOC_FAIL] = "swpin_mismatch_order0_origin_anon_fallback_alloc_fail",
	[MTHP_EXP_SWPIN_MISMATCH_ORDER0_ORIGIN_ANON_FALLBACK_CHARGE_FAIL] = "swpin_mismatch_order0_origin_anon_fallback_charge_fail",
	[MTHP_EXP_SWPIN_MISMATCH_ORDER0_ORIGIN_ANON_FALLBACK_ALL_ORDERS_FAILED] = "swpin_mismatch_order0_origin_anon_fallback_all_orders_failed",
	[MTHP_EXP_SWPIN_MISMATCH_ORDER0_ORIGIN_SWPIN_ORDER0] = "swpin_mismatch_order0_origin_swpin_order0",
	[MTHP_EXP_SWPIN_MISMATCH_ORDER0_ORIGIN_UNKNOWN] = "swpin_mismatch_order0_origin_unknown",
	[MTHP_EXP_SWPIN_MISMATCH_CASE_B_TRANSFER_FORK_COPY_NONPRESENT] = "swpin_mismatch_case_b_transfer_fork_copy_nonpresent",
	[MTHP_EXP_SWPIN_MISMATCH_CASE_B_TRANSFER_MREMAP_MOVE_PTES] = "swpin_mismatch_case_b_transfer_mremap_move_ptes",
	[MTHP_EXP_SWPIN_MISMATCH_CASE_B_TRANSFER_UNKNOWN] = "swpin_mismatch_case_b_transfer_unknown",
};

void mthp_exp_record_folio_origin(struct folio *folio,
				  enum mthp_exp_folio_origin origin,
				  unsigned int subreason,
				  struct vm_area_struct *vma,
				  unsigned long addr)
{
	struct mthp_exp_folio_origin_record *slot;
	unsigned long pfn;
	unsigned long flags;

	if (!folio)
		return;

	pfn = folio_pfn(folio);
	slot = &mthp_exp_folio_origins[mthp_exp_folio_idx(pfn)];

	spin_lock_irqsave(&slot->lock, flags);
	slot->valid = true;
	slot->pfn = pfn;
	slot->mm = (unsigned long)(vma ? vma->vm_mm : current->mm);
	slot->addr = addr;
	slot->jiffies = jiffies;
	slot->origin = origin;
	slot->subreason = subreason;
	slot->pid = current->pid;
	slot->tgid = current->tgid;
	mthp_exp_copy_task(slot->comm, slot->leader_comm);
	mthp_exp_copy_vma_name(slot->vma_name, vma);
	spin_unlock_irqrestore(&slot->lock, flags);
}

void mthp_exp_record_swap_pte_birth(swp_entry_t entry, struct folio *folio,
				    struct page *subpage,
				    struct vm_area_struct *vma,
				    unsigned long addr)
{
	struct mthp_exp_folio_origin_record origin = {};
	struct mthp_exp_swap_birth_record *rec;
	struct mthp_exp_swap_slot *slot;
	unsigned long key = mthp_exp_swap_key(entry);
	unsigned long flags;
	unsigned long seq;
	unsigned int subpage_idx = 0;

	if (!folio)
		return;

	if (subpage)
		subpage_idx = page_to_pfn(subpage) - folio_pfn(folio);

	slot = &mthp_exp_swap_births[mthp_exp_swap_idx(key)];

	spin_lock_irqsave(&slot->lock, flags);
	slot->valid = true;
	slot->key = key;
	slot->seq = atomic64_inc_return(&mthp_exp_birth_seq);
	seq = slot->seq;
	rec = &slot->rec;
	memset(rec, 0, sizeof(*rec));
	rec->found = true;
	rec->seq = slot->seq;
	rec->birth_jiffies = jiffies;
	rec->birth_addr = addr;
	rec->birth_addr_mod4 = (addr >> PAGE_SHIFT) & 3;
	rec->swp_offset = swp_offset(entry);
	rec->swp_mod4 = swp_offset(entry) & 3;
	rec->birth_mm = (unsigned long)(vma ? vma->vm_mm : current->mm);
	rec->birth_vma_start = vma ? vma->vm_start : 0;
	rec->birth_vma_end = vma ? vma->vm_end : 0;
	rec->birth_folio_pfn = folio_pfn(folio);
	rec->birth_folio_order = folio_order(folio);
	rec->birth_subpage_idx = subpage_idx;
	rec->birth_pid = current->pid;
	rec->birth_tgid = current->tgid;
	mthp_exp_copy_task(rec->birth_comm, rec->birth_leader_comm);
	mthp_exp_copy_vma_name(rec->birth_vma_name, vma);
	rec->birth_origin = MTHP_EXP_ORIGIN_UNKNOWN;
	rec->transfer_reason = MTHP_EXP_TRANSFER_NONE;
	spin_unlock_irqrestore(&slot->lock, flags);

	if (mthp_exp_lookup_folio_origin(folio, &origin)) {
		spin_lock_irqsave(&slot->lock, flags);
		if (slot->valid && slot->key == key && slot->seq == seq) {
			slot->rec.birth_origin = origin.origin;
			slot->rec.birth_origin_subreason = origin.subreason;
			if (origin.vma_name[0])
				strscpy(slot->rec.birth_vma_name, origin.vma_name,
					MTHP_EXP_NAME_LEN);
		}
		spin_unlock_irqrestore(&slot->lock, flags);
	}
}

void mthp_exp_record_swap_pte_transfer(swp_entry_t entry,
				       enum mthp_exp_swap_transfer reason,
				       struct mm_struct *old_mm,
				       struct mm_struct *new_mm,
				       unsigned long old_addr,
				       unsigned long new_addr)
{
	struct mthp_exp_swap_slot *slot;
	unsigned long key = mthp_exp_swap_key(entry);
	unsigned long flags;

	slot = &mthp_exp_swap_births[mthp_exp_swap_idx(key)];
	spin_lock_irqsave(&slot->lock, flags);
	if (slot->valid && slot->key == key) {
		slot->rec.transfer_reason = reason;
		slot->rec.transfer_old_mm = (unsigned long)old_mm;
		slot->rec.transfer_new_mm = (unsigned long)new_mm;
		slot->rec.transfer_old_addr = old_addr;
		slot->rec.transfer_new_addr = new_addr;
		slot->rec.transfer_old_mod4 = (old_addr >> PAGE_SHIFT) & 3;
		slot->rec.transfer_new_mod4 = (new_addr >> PAGE_SHIFT) & 3;
		slot->rec.transfer_pid = current->pid;
		slot->rec.transfer_tgid = current->tgid;
		memcpy(slot->rec.transfer_comm, current->comm, TASK_COMM_LEN);
	}
	spin_unlock_irqrestore(&slot->lock, flags);
}

bool mthp_exp_lookup_swap_birth(swp_entry_t entry,
				struct mthp_exp_swap_birth_record *record)
{
	struct mthp_exp_swap_slot *slot;
	unsigned long key = mthp_exp_swap_key(entry);
	unsigned long flags;
	bool found = false;

	memset(record, 0, sizeof(*record));
	slot = &mthp_exp_swap_births[mthp_exp_swap_idx(key)];
	spin_lock_irqsave(&slot->lock, flags);
	if (slot->valid && slot->key == key) {
		*record = slot->rec;
		record->found = true;
		found = true;
	}
	spin_unlock_irqrestore(&slot->lock, flags);
	return found;
}

static void mthp_exp_count_order0_origin(unsigned int origin)
{
	switch (origin) {
	case MTHP_EXP_ORIGIN_UFFD_COPY:
		mthp_exp_inc(MTHP_EXP_SWPIN_MISMATCH_ORDER0_ORIGIN_UFFD_COPY);
		break;
	case MTHP_EXP_ORIGIN_UFFD_ZEROPAGE:
		mthp_exp_inc(MTHP_EXP_SWPIN_MISMATCH_ORDER0_ORIGIN_UFFD_ZEROPAGE);
		break;
	case MTHP_EXP_ORIGIN_COW:
		mthp_exp_inc(MTHP_EXP_SWPIN_MISMATCH_ORDER0_ORIGIN_COW);
		break;
	case MTHP_EXP_ORIGIN_ANON_FALLBACK_UFFD:
		mthp_exp_inc(MTHP_EXP_SWPIN_MISMATCH_ORDER0_ORIGIN_ANON_FALLBACK_UFFD);
		break;
	case MTHP_EXP_ORIGIN_ANON_FALLBACK_ALLOWABLE:
		mthp_exp_inc(MTHP_EXP_SWPIN_MISMATCH_ORDER0_ORIGIN_ANON_FALLBACK_ALLOWABLE);
		break;
	case MTHP_EXP_ORIGIN_ANON_FALLBACK_VMA_SUITABLE:
		mthp_exp_inc(MTHP_EXP_SWPIN_MISMATCH_ORDER0_ORIGIN_ANON_FALLBACK_VMA_SUITABLE);
		break;
	case MTHP_EXP_ORIGIN_ANON_FALLBACK_PTE_OCCUPIED:
		mthp_exp_inc(MTHP_EXP_SWPIN_MISMATCH_ORDER0_ORIGIN_ANON_FALLBACK_PTE_OCCUPIED);
		break;
	case MTHP_EXP_ORIGIN_ANON_FALLBACK_ALLOC_FAIL:
		mthp_exp_inc(MTHP_EXP_SWPIN_MISMATCH_ORDER0_ORIGIN_ANON_FALLBACK_ALLOC_FAIL);
		break;
	case MTHP_EXP_ORIGIN_ANON_FALLBACK_CHARGE_FAIL:
		mthp_exp_inc(MTHP_EXP_SWPIN_MISMATCH_ORDER0_ORIGIN_ANON_FALLBACK_CHARGE_FAIL);
		break;
	case MTHP_EXP_ORIGIN_ANON_FALLBACK_ALL_ORDERS_FAILED:
		mthp_exp_inc(MTHP_EXP_SWPIN_MISMATCH_ORDER0_ORIGIN_ANON_FALLBACK_ALL_ORDERS_FAILED);
		break;
	case MTHP_EXP_ORIGIN_SWPIN_ORDER0_FALLBACK:
		mthp_exp_inc(MTHP_EXP_SWPIN_MISMATCH_ORDER0_ORIGIN_SWPIN_ORDER0);
		break;
	default:
		mthp_exp_inc(MTHP_EXP_SWPIN_MISMATCH_ORDER0_ORIGIN_UNKNOWN);
		break;
	}
}

static void mthp_exp_count_case_b_transfer(unsigned int reason)
{
	switch (reason) {
	case MTHP_EXP_TRANSFER_FORK_COPY_NONPRESENT:
		mthp_exp_inc(MTHP_EXP_SWPIN_MISMATCH_CASE_B_TRANSFER_FORK_COPY_NONPRESENT);
		break;
	case MTHP_EXP_TRANSFER_MREMAP_MOVE_PTES:
		mthp_exp_inc(MTHP_EXP_SWPIN_MISMATCH_CASE_B_TRANSFER_MREMAP_MOVE_PTES);
		break;
	default:
		mthp_exp_inc(MTHP_EXP_SWPIN_MISMATCH_CASE_B_TRANSFER_UNKNOWN);
		break;
	}
}

unsigned int mthp_exp_classify_swap_mismatch(
		swp_entry_t entry, struct vm_area_struct *vma,
		unsigned long fault_addr,
		struct mthp_exp_swap_birth_record *record)
{
	unsigned long fault_mod4 = (fault_addr >> PAGE_SHIFT) & 3;

	if (!mthp_exp_lookup_swap_birth(entry, record)) {
		mthp_exp_inc(MTHP_EXP_SWPIN_MISMATCH_BIRTH_NOT_FOUND);
		return MTHP_EXP_MISMATCH_BIRTH_NOT_FOUND;
	}

	mthp_exp_inc(MTHP_EXP_SWPIN_MISMATCH_BIRTH_FOUND);
	record->stale = vma && record->birth_mm &&
		record->birth_mm != (unsigned long)vma->vm_mm &&
		record->transfer_reason == MTHP_EXP_TRANSFER_NONE;
	if (record->stale) {
		mthp_exp_inc(MTHP_EXP_SWPIN_MISMATCH_BIRTH_STALE);
		return MTHP_EXP_MISMATCH_BIRTH_STALE;
	}

	if (record->birth_folio_order == 0) {
		mthp_exp_inc(MTHP_EXP_SWPIN_MISMATCH_BIRTH_ORDER0);
		mthp_exp_inc(MTHP_EXP_SWPIN_MISMATCH_CASE_C_ENTRY_ORDER0);
		mthp_exp_count_order0_origin(record->birth_origin);
		return MTHP_EXP_MISMATCH_CASE_C_ORDER0;
	}

	if (record->birth_folio_order == 2) {
		mthp_exp_inc(MTHP_EXP_SWPIN_MISMATCH_BIRTH_ORDER2);
		if (record->birth_addr_mod4 != record->swp_mod4) {
			mthp_exp_inc(MTHP_EXP_SWPIN_MISMATCH_CASE_A_BIRTH_PHASE_BAD);
			return MTHP_EXP_MISMATCH_CASE_A_BIRTH_PHASE_BAD;
		}
		if (fault_mod4 != record->swp_mod4) {
			mthp_exp_inc(MTHP_EXP_SWPIN_MISMATCH_CASE_B_BIRTH_GOOD_NOW_BAD);
			mthp_exp_count_case_b_transfer(record->transfer_reason);
			return MTHP_EXP_MISMATCH_CASE_B_BIRTH_GOOD_NOW_BAD;
		}
	}

	mthp_exp_inc(MTHP_EXP_SWPIN_MISMATCH_BIRTH_OTHER_ORDER);
	return MTHP_EXP_MISMATCH_BIRTH_OTHER_ORDER;
}

static int mthp_exp_counters_show(struct seq_file *m, void *v)
{
	int i;

	for (i = 0; i < MTHP_EXP_NR_COUNTERS; i++)
		seq_printf(m, "%s %lld\n", mthp_exp_counter_names[i],
			   atomic64_read(&mthp_exp_counters[i]));
	return 0;
}

static int mthp_exp_counters_open(struct inode *inode, struct file *file)
{
	return single_open(file, mthp_exp_counters_show, NULL);
}

static const struct file_operations mthp_exp_counters_fops = {
	.owner = THIS_MODULE,
	.open = mthp_exp_counters_open,
	.read = seq_read,
	.llseek = seq_lseek,
	.release = single_release,
};

static const struct proc_ops mthp_exp_counters_proc_ops = {
	.proc_open = mthp_exp_counters_open,
	.proc_read = seq_read,
	.proc_lseek = seq_lseek,
	.proc_release = single_release,
};

static int __init mthp_exp_debugfs_init(void)
{
	unsigned int i;

	for (i = 0; i < MTHP_EXP_FOLIO_SLOTS; i++)
		spin_lock_init(&mthp_exp_folio_origins[i].lock);
	for (i = 0; i < MTHP_EXP_SWAP_SLOTS; i++)
		spin_lock_init(&mthp_exp_swap_births[i].lock);

	debugfs_create_file("mthp_reason_counters", 0400, NULL, NULL,
			    &mthp_exp_counters_fops);
	proc_create("mthp_reason_counters", 0444, NULL,
		    &mthp_exp_counters_proc_ops);
	return 0;
}
late_initcall(mthp_exp_debugfs_init);

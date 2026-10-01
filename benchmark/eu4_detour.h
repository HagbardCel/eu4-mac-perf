#ifndef EU4_DETOUR_H
#define EU4_DETOUR_H

#include <libkern/OSCacheControl.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

typedef struct {
    unsigned char bytes[32];
    void *trampoline;
    uintptr_t site;
    size_t patch;
} EU4Detour;

static inline bool eu4_detour_boundaries_valid(size_t patch,
        const uint8_t *lengths,size_t count) {
    if(patch<13 || patch>32) return false;
    if(!lengths) return true;
    size_t sum=0;
    for(size_t i=0;i<count;i++) {
        if(!lengths[i] || lengths[i]>15 || lengths[i]>patch-sum) return false;
        sum+=lengths[i];
    }
    return sum==patch;
}

static inline void eu4_detour_jump(unsigned char *at,uintptr_t target) {
    at[0]=0x49; at[1]=0xbb; memcpy(at+2,&target,8);
    at[10]=0x41; at[11]=0xff; at[12]=0xe3;
}

static inline bool eu4_detour_restore(EU4Detour *detour);

static inline bool eu4_detour_install(EU4Detour *detour,void *site,size_t patch,
        const unsigned char *expected,void *replacement,const uint8_t *lengths,
        size_t instruction_count) {
    if(!detour || !site || !expected || !replacement || detour->trampoline ||
       !eu4_detour_boundaries_valid(patch,lengths,instruction_count) ||
       memcmp(site,expected,patch)!=0) return false;
    long page=sysconf(_SC_PAGESIZE);
    uintptr_t address=(uintptr_t)site;
    uintptr_t begin=address&~((uintptr_t)page-1);
    if(address+(uintptr_t)patch>begin+(uintptr_t)page) return false;
    if(mach_vm_protect(mach_task_self(),begin,(mach_vm_size_t)page,false,
                       VM_PROT_READ|VM_PROT_WRITE|VM_PROT_COPY)!=KERN_SUCCESS) return false;
    void *trampoline=mmap(NULL,4096,PROT_READ|PROT_WRITE|PROT_EXEC,
                          MAP_PRIVATE|MAP_ANON,-1,0);
    if(trampoline==MAP_FAILED) {
        (void)mach_vm_protect(mach_task_self(),begin,(mach_vm_size_t)page,false,
                              VM_PROT_READ|VM_PROT_EXECUTE);
        return false;
    }
    memcpy(trampoline,site,patch);
    eu4_detour_jump((unsigned char *)trampoline+patch,address+patch);
    sys_icache_invalidate(trampoline,patch+13);
    memcpy(detour->bytes,site,patch); detour->site=address;
    detour->patch=patch; detour->trampoline=trampoline;
    eu4_detour_jump(site,(uintptr_t)replacement);
    for(size_t i=13;i<patch;i++) ((unsigned char *)site)[i]=0x90;
    sys_icache_invalidate(site,patch);
    if(mach_vm_protect(mach_task_self(),begin,(mach_vm_size_t)page,false,
                       VM_PROT_READ|VM_PROT_EXECUTE)!=KERN_SUCCESS) {
        (void)eu4_detour_restore(detour); return false;
    }
    return true;
}

static inline bool eu4_detour_restore(EU4Detour *detour) {
    if(!detour || !detour->site || !detour->trampoline || detour->trampoline==MAP_FAILED)
        return false;
    long page=sysconf(_SC_PAGESIZE);
    uintptr_t begin=detour->site&~((uintptr_t)page-1);
    if(mach_vm_protect(mach_task_self(),begin,(mach_vm_size_t)page,false,
                       VM_PROT_READ|VM_PROT_WRITE|VM_PROT_COPY)!=KERN_SUCCESS) return false;
    memcpy((void *)detour->site,detour->bytes,detour->patch);
    sys_icache_invalidate((void *)detour->site,detour->patch);
    if(mach_vm_protect(mach_task_self(),begin,(mach_vm_size_t)page,false,
                       VM_PROT_READ|VM_PROT_EXECUTE)!=KERN_SUCCESS) return false;
    (void)munmap(detour->trampoline,4096);
    memset(detour,0,sizeof(*detour));
    return true;
}
/* Roll back in reverse install order, preserving any failed restoration state. */
static inline bool eu4_detour_rollback(EU4Detour *detours,unsigned count) {
    bool valid=true;
    while(count) {
        EU4Detour *d=&detours[--count];
        if(d->site && !eu4_detour_restore(d)) valid=false;
    }
    return valid;
}

#endif

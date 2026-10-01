#include "../benchmark/eu4_detour.h"
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

typedef long (*LongFn)(long);
typedef long (*MixedFn)(const int *,int,float,bool);
static LongFn long_original;
static MixedFn mixed_original;

static LongFn synthetic_long;
static MixedFn synthetic_mixed;
static long replace_long(long value) {
    if(value==9) return synthetic_long(4)+long_original(value);
    return long_original(value)+100;
}
static long replace_mixed(const int *pointer,int integer,float real,bool flag) {
    return mixed_original(pointer,integer,real,flag)+100;
}

static void *make_code(const unsigned char *code,size_t length) {
    void *memory=mmap(NULL,4096,PROT_READ|PROT_WRITE|PROT_EXEC,
                      MAP_PRIVATE|MAP_ANON,-1,0);
    if(memory==MAP_FAILED) return NULL;
    memcpy(memory,code,length); sys_icache_invalidate(memory,length);
    return memory;
}
static int install(EU4Detour *detour,void *site,void *replacement,
                   const uint8_t lengths[3]) {
    unsigned char bytes[13]; memcpy(bytes,site,sizeof(bytes));
    return eu4_detour_install(detour,site,sizeof(bytes),bytes,replacement,lengths,3);
}
int main(void) {
    const uint8_t lengths[3]={1,3,9},bad_lengths[3]={1,3,8};
    EU4Detour first={0},second={0};
    static const unsigned char long_code[]={0x55,0x48,0x89,0xe5,
        0x90,0x90,0x90,0x90,0x90,0x90,0x90,0x90,0x90,
        0x89,0xf8,0x83,0xc0,0x07,0x5d,0xc3};
    static const unsigned char mixed_code[]={0x55,0x48,0x89,0xe5,
        0x90,0x90,0x90,0x90,0x90,0x90,0x90,0x90,0x90,
        0x8b,0x07,0x01,0xf0,0xf3,0x0f,0x2c,0xc8,0x01,0xc8,0x01,0xd0,0x5d,0xc3};
    void *long_code_mem=make_code(long_code,sizeof(long_code));
    void *mixed_code_mem=make_code(mixed_code,sizeof(mixed_code));
    if(!long_code_mem || !mixed_code_mem) return 1;
    synthetic_long=(LongFn)long_code_mem; synthetic_mixed=(MixedFn)mixed_code_mem;
    long before=synthetic_long(4);
    unsigned char wrong[13]; memcpy(wrong,long_code_mem,sizeof(wrong));
    wrong[0]^=0xff;
    if(eu4_detour_install(&first,long_code_mem,13,wrong,
                          (void *)replace_long,lengths,3)) return 2;
    if(eu4_detour_boundaries_valid(13,bad_lengths,3)) return 3;
    if(!install(&first,long_code_mem,(void *)replace_long,lengths)) return 4;
    long_original=(LongFn)first.trampoline;
    if(synthetic_long(4)!=before+100 || synthetic_long(9)!=127) return 5;
    if(!eu4_detour_restore(&first) || synthetic_long(4)!=before) return 6;

    int value=17;
    long mixed_before=synthetic_mixed(&value,5,3.0f,true);
    if(!install(&second,mixed_code_mem,(void *)replace_mixed,lengths)) return 7;
    mixed_original=(MixedFn)second.trampoline;
    if(synthetic_mixed(&value,5,3.0f,true)!=mixed_before+100) return 8;
    if(!eu4_detour_restore(&second) ||
       synthetic_mixed(&value,5,3.0f,true)!=mixed_before) return 9;
    (void)munmap(long_code_mem,4096); (void)munmap(mixed_code_mem,4096);
    puts("detour trampoline, forwarding, recursion, boundary rejection, and restore passed");
    return 0;
}

#include <assert.h>
#include <dlfcn.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <unistd.h>
int main(int argc,char **argv) {
    if(argc!=2) return 1;
    void *library=dlopen(argv[1],RTLD_NOW);assert(library);
    unsigned (*observe)(void)=dlsym(library,"eu4_frame_model_test_observe_unowned");assert(observe);
    int fd=open(getenv("EU4_FRAME_MODEL_CONTROL"),O_RDWR);assert(fd>=0);
    uint32_t *command=mmap(NULL,4096,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);assert(command!=MAP_FAILED);
    uint64_t *generation=(uint64_t *)((char *)command+40),*epoch=(uint64_t *)((char *)command+48);
    assert(observe()==0);
    __atomic_store_n(&command[1],3,__ATOMIC_RELEASE);
    command[2]=1;command[3]=1;command[4]=3;*generation=2;*epoch=7;
    __atomic_store_n(&command[1],4,__ATOMIC_RELEASE);
    assert(observe()==257);assert(observe()==257);
    __atomic_store_n(&command[1],5,__ATOMIC_RELEASE);
    command[2]=3;command[3]=4;*generation=3;*epoch=8;
    __atomic_store_n(&command[1],6,__ATOMIC_RELEASE);
    assert(observe()==259);assert(observe()==259);
    __atomic_store_n(&command[1],7,__ATOMIC_RELEASE);
    command[2]=0;command[3]=0;command[4]=0;*generation=4;*epoch=0;
    __atomic_store_n(&command[1],8,__ATOMIC_RELEASE);
    assert(observe()==0);
    munmap(command,4096);close(fd);dlclose(library);
    puts("unowned thread observes profile, draw suppression, and measurement-off commands");
}

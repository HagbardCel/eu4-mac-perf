#define GL_SILENCE_DEPRECATION 1
#include <OpenGL/OpenGL.h>
#include <OpenGL/gl3.h>
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

int main(int argc,char **argv) {
    unsigned long loops=argc>1?strtoul(argv[1],NULL,10):12000;
    if(loops<1000 || loops>200000) return 6;
    CGLPixelFormatAttribute attrs[]={kCGLPFAAccelerated,0};
    CGLPixelFormatObj pf=NULL; CGLContextObj ctx=NULL; GLint n=0;
    if(CGLChoosePixelFormat(attrs,&pf,&n)!=kCGLNoError || !pf) return 2;
    if(CGLCreateContext(pf,NULL,&ctx)!=kCGLNoError || !ctx) return 3;
    if(CGLSetCurrentContext(ctx)!=kCGLNoError) return 4;
    void (*draw)(GLenum,GLint,GLsizei)=dlsym(RTLD_DEFAULT,"glDrawArrays");
    if(!draw) return 5;
    for(unsigned i=0;i<2000;i++) glEnable(GL_BLEND);
    uint64_t start=0,thread_start=0; struct timespec ts;
    clock_gettime(CLOCK_THREAD_CPUTIME_ID,&ts);
    thread_start=(uint64_t)ts.tv_sec*1000000000ull+ts.tv_nsec;
    clock_gettime(CLOCK_UPTIME_RAW,&ts);
    start=(uint64_t)ts.tv_sec*1000000000ull+ts.tv_nsec;
    for(unsigned long i=0;i<loops;i++) { glEnable(GL_BLEND); draw(GL_TRIANGLES,0,3); }
    clock_gettime(CLOCK_UPTIME_RAW,&ts);
    uint64_t end=(uint64_t)ts.tv_sec*1000000000ull+ts.tv_nsec;
    clock_gettime(CLOCK_THREAD_CPUTIME_ID,&ts);
    uint64_t thread_end=(uint64_t)ts.tv_sec*1000000000ull+ts.tv_nsec;
    printf("draws=%lu elapsed_ns=%llu cpu_ns=%llu\n",loops,
           (unsigned long long)(end-start),(unsigned long long)(thread_end-thread_start));
    CGLFlushDrawable(ctx); CGLSetCurrentContext(NULL);
    CGLDestroyContext(ctx); CGLDestroyPixelFormat(pf); return 0;
}

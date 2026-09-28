#define GL_SILENCE_DEPRECATION 1
#include <OpenGL/OpenGL.h>
#include <OpenGL/gl.h>
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-W#warnings"
#include <OpenGL/gl3.h>
#pragma clang diagnostic pop
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

typedef void (*UseProgram)(GLhandleARB);
typedef void (*Uniform1i)(GLint,GLint);
typedef void (*Uniform4fv)(GLint,GLsizei,const GLfloat *);
typedef void (*LinkProgram)(GLhandleARB);

static GLuint shader(GLenum kind,const char *source) {
    GLuint value=glCreateShader(kind);
    glShaderSource(value,1,&source,NULL);
    glCompileShader(value);
    GLint okay=0;
    glGetShaderiv(value,GL_COMPILE_STATUS,&okay);
    return okay ? value : 0;
}
static int set_mode(unsigned char mode) {
    const char *path=getenv("EU4_SAMPLER_CONTROL");
    FILE *file=path ? fopen(path,"r+b") : NULL;
    if (!file) return 0;
    int okay=fputc(mode,file)!=EOF;
    fclose(file);
    return okay;
}
static uint64_t now_ns(void) {
    struct timespec time;
    clock_gettime(CLOCK_UPTIME_RAW,&time);
    return (uint64_t)time.tv_sec*1000000000ull+time.tv_nsec;
}
int main(int argc,char **argv) {
    CGLPixelFormatAttribute attrs[]={kCGLPFAAccelerated,(CGLPixelFormatAttribute)0};
    CGLPixelFormatObj format=NULL;
    CGLContextObj context=NULL;
    GLint count=0;
    if (CGLChoosePixelFormat(attrs,&format,&count)!=kCGLNoError || !format) return 2;
    if (CGLCreateContext(format,NULL,&context)!=kCGLNoError || !context) return 3;
    if (CGLSetCurrentContext(context)!=kCGLNoError) return 4;
    UseProgram use=(UseProgram)dlsym(RTLD_DEFAULT,"glUseProgramObjectARB");
    Uniform1i uniform=(Uniform1i)dlsym(RTLD_DEFAULT,"glUniform1i");
    Uniform4fv other=(Uniform4fv)dlsym(RTLD_DEFAULT,"glUniform4fvARB");
    LinkProgram relink=(LinkProgram)dlsym(RTLD_DEFAULT,"glLinkProgramARB");
    if (!use || !uniform || !other || !relink) return 5;
    GLuint vertex=shader(GL_VERTEX_SHADER,"void main(){gl_Position=gl_Vertex;}");
    GLuint fragment=shader(GL_FRAGMENT_SHADER,
        "uniform int index; uniform vec4 color; "
        "void main(){gl_FragColor=color*float(index);}");
    if (!vertex || !fragment) return 6;
    GLuint program=glCreateProgram();
    glAttachShader(program,vertex);glAttachShader(program,fragment);glLinkProgram(program);
    GLint index=glGetUniformLocation(program,"index");
    GLint color=glGetUniformLocation(program,"color");
    if (index<0 || color<0) return 7;
    CGLFlushDrawable(context); // Establish the rendering thread.
    use((GLhandleARB)(uintptr_t)program);
    if (argc>1 && !strcmp(argv[1],"--contexts")) {
        if (!set_mode(1)) return 13;
        CGLFlushDrawable(context);
        CGLContextObj second=NULL;
        if (CGLCreateContext(format,context,&second)!=kCGLNoError || !second) return 14;
        if (CGLSetCurrentContext(second)!=kCGLNoError) return 15;
        use((GLhandleARB)(uintptr_t)program);
        for (int i=0;i<100;i++) uniform(index,2);
        if (CGLSetCurrentContext(context)!=kCGLNoError) return 16;
        use((GLhandleARB)(uintptr_t)program);
        uniform(index,2);
        usleep(1100000);CGLFlushDrawable(context);
        return 0;
    }
    if (argc>1 && !strcmp(argv[1],"--stress")) {
        uint64_t start=now_ns();
        for (int i=0;i<1000000;i++) uniform(index,2);
        printf("stress_ns=%llu\n",(unsigned long long)(now_ns()-start));
        return 0;
    }
    for (int i=0;i<1000;i++) uniform(index,2);
    usleep(1100000);CGLFlushDrawable(context); // OFF: 1000 forwarded.
    if (!set_mode(1)) return 8;
    CGLFlushDrawable(context);
    use((GLhandleARB)(uintptr_t)program);
    for (int i=0;i<1000;i++) uniform(index,2);
    GLfloat rgba[4]={.25f,.5f,.75f,1.f};
    other(color,1,rgba);
    uniform(index,2); // Unrelated write must not evict index.
    uniform(index,3);
    uniform(index,2); // Changed value must be forwarded.
    GLint actual=0;
    glGetUniformiv(program,index,&actual);
    if (actual!=2) return 9;
    usleep(1100000);CGLFlushDrawable(context);
    if (!set_mode(0)) return 10;
    CGLFlushDrawable(context);
    uniform(index,2);
    usleep(1100000);CGLFlushDrawable(context);
    if (!set_mode(1)) return 11;
    CGLFlushDrawable(context);
    use((GLhandleARB)(uintptr_t)program);
    uniform(index,2); // Mode switch must start cold.
    relink((GLhandleARB)(uintptr_t)program);
    use((GLhandleARB)(uintptr_t)program);
    uniform(index,2); // Program relink must invalidate.
    glGetUniformiv(program,index,&actual);
    if (actual!=2) return 12;
    usleep(1100000);CGLFlushDrawable(context);
    puts("sampler pass-through, deduplication, invalidation, and transitions passed");
    return 0;
}

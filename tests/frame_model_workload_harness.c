#define GL_SILENCE_DEPRECATION 1
#include <OpenGL/OpenGL.h>
#include <OpenGL/gl3.h>
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
typedef struct { uint32_t api,mode,count,uniform,texture,state,vertex,program; } Command;
static Command *commands; static unsigned command_count;
static GLuint program,texture,vbo,ibo; static GLint color;
static uint64_t now(clockid_t clock) { struct timespec t;clock_gettime(clock,&t);return (uint64_t)t.tv_sec*1000000000ull+t.tv_nsec; }
static GLuint shader(GLenum type,const char *source) {
    GLuint id=glCreateShader(type);glShaderSource(id,1,&source,NULL);glCompileShader(id);
    GLint ok=0;glGetShaderiv(id,GL_COMPILE_STATUS,&ok);if(!ok) exit(7);return id;
}
static void workload(void) {
    for(unsigned i=0;i<command_count;i++) {
        Command c=commands[i];
        if(c.program) glUseProgram(program);
        if(c.uniform) { GLfloat v[]={.25f,.5f,.75f,1.f};glUniform4fv(color,1,v); }
        if(c.texture) glBindTexture(GL_TEXTURE_2D,texture);
        if(c.state) { glEnable(GL_BLEND);glBlendFunc(GL_SRC_ALPHA,GL_ONE_MINUS_SRC_ALPHA); }
        if(c.vertex) { glBindBuffer(GL_ARRAY_BUFFER,vbo);glBindBuffer(GL_ELEMENT_ARRAY_BUFFER,ibo);glVertexAttribPointer(0,2,GL_FLOAT,GL_FALSE,0,0); }
        if(c.api==1) glDrawElements(c.mode,c.count,GL_UNSIGNED_INT,0);
        else if(c.api==2) glDrawElementsBaseVertex(c.mode,c.count,GL_UNSIGNED_INT,0,0);
        else glDrawArrays(c.mode,0,c.count);
    }
}
int main(int argc,char **argv) {
    if(argc!=3) return 1;
    FILE *file=fopen(argv[1],"rb");if(!file) return 1;
    fseek(file,0,SEEK_END);long bytes=ftell(file);rewind(file);
    if(bytes<=0 || bytes%(long)sizeof(Command)) return 1;
    command_count=(unsigned)(bytes/sizeof(Command));commands=malloc((size_t)bytes);
    if(fread(commands,sizeof(Command),command_count,file)!=command_count) return 1;fclose(file);
    unsigned maximum=0;for(unsigned i=0;i<command_count;i++) if(commands[i].count>maximum) maximum=commands[i].count;
    CGLPixelFormatAttribute attrs[]={kCGLPFAAccelerated,kCGLPFAOpenGLProfile,(CGLPixelFormatAttribute)kCGLOGLPVersion_3_2_Core,0};
    CGLPixelFormatObj pf=NULL;CGLContextObj ctx=NULL;GLint n=0;
    if(CGLChoosePixelFormat(attrs,&pf,&n)!=kCGLNoError || !pf) return 2;
    if(CGLCreateContext(pf,NULL,&ctx)!=kCGLNoError || !ctx) return 3;
    if(CGLSetCurrentContext(ctx)!=kCGLNoError) return 4;
    const char *vs="#version 150\nin vec2 position; void main(){gl_Position=vec4(position,0,1);}";
    const char *fs="#version 150\nuniform vec4 color; out vec4 result; void main(){result=color;}";
    GLuint vert=shader(GL_VERTEX_SHADER,vs),frag=shader(GL_FRAGMENT_SHADER,fs);
    program=glCreateProgram();glAttachShader(program,vert);glAttachShader(program,frag);glBindAttribLocation(program,0,"position");glLinkProgram(program);
    GLint ok=0;glGetProgramiv(program,GL_LINK_STATUS,&ok);if(!ok) return 8;
    glUseProgram(program);color=glGetUniformLocation(program,"color");
    GLuint vao;glGenVertexArrays(1,&vao);glBindVertexArray(vao);
    GLfloat *vertices=calloc((size_t)maximum*2,sizeof(GLfloat));GLuint *indices=calloc(maximum,sizeof(GLuint));
    if(!vertices || !indices) return 9;
    for(unsigned i=0;i<maximum;i++) { vertices[2*i]=(i%3==1)?.5f:-.5f;vertices[2*i+1]=(i%3==2)?.5f:-.5f;indices[i]=i; }
    glGenBuffers(1,&vbo);glBindBuffer(GL_ARRAY_BUFFER,vbo);glBufferData(GL_ARRAY_BUFFER,(GLsizeiptr)maximum*2*sizeof(GLfloat),vertices,GL_STATIC_DRAW);
    glGenBuffers(1,&ibo);glBindBuffer(GL_ELEMENT_ARRAY_BUFFER,ibo);glBufferData(GL_ELEMENT_ARRAY_BUFFER,(GLsizeiptr)maximum*sizeof(GLuint),indices,GL_STATIC_DRAW);
    glVertexAttribPointer(0,2,GL_FLOAT,GL_FALSE,0,0);glEnableVertexAttribArray(0);
    glGenTextures(1,&texture);glBindTexture(GL_TEXTURE_2D,texture);glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA8,64,64,0,GL_RGBA,GL_UNSIGNED_BYTE,NULL);
    GLuint fbo;glGenFramebuffers(1,&fbo);glBindFramebuffer(GL_FRAMEBUFFER,fbo);glFramebufferTexture2D(GL_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_2D,texture,0);
    if(glCheckFramebufferStatus(GL_FRAMEBUFFER)!=GL_FRAMEBUFFER_COMPLETE) return 10;
    glViewport(0,0,64,64);
    void (*frame)(void (*)(void))=dlsym(RTLD_DEFAULT,"eu4_frame_model_test_frame");
    unsigned frames=(unsigned)strtoul(argv[2],NULL,10);if(frames<4 || frames>60) return 1;
    uint64_t preparation_cpu=now(CLOCK_THREAD_CPUTIME_ID),preparation_wall=now(CLOCK_UPTIME_RAW);
    if(frame) frame(workload);else workload();
    preparation_wall=now(CLOCK_UPTIME_RAW)-preparation_wall;
    preparation_cpu=now(CLOCK_THREAD_CPUTIME_ID)-preparation_cpu;
    glFinish();GLenum warm_error=glGetError();if(warm_error!=GL_NO_ERROR) {fprintf(stderr,"warmup GL error=%u\n",warm_error);return 11;}
    void (*arm)(unsigned)=dlsym(RTLD_DEFAULT,"eu4_frame_model_test_arm");
    if(arm) arm(getenv("EU4_TEST_SAMPLED")?frames:0);
    const char *completion_timing=getenv("EU4_TEST_COMPLETION_TIMING");
    int report_completion=completion_timing && strcmp(completion_timing,"0");
    uint64_t cpu=now(CLOCK_THREAD_CPUTIME_ID),wall=now(CLOCK_UPTIME_RAW);
    for(unsigned i=0;i<frames;i++) { if(frame) frame(workload);else workload(); }
    uint64_t submission_elapsed=now(CLOCK_UPTIME_RAW)-wall,submission_cpu=now(CLOCK_THREAD_CPUTIME_ID)-cpu;
    uint64_t drain_elapsed=0,drain_cpu=0;
    if(report_completion) {
        uint64_t drain_wall=now(CLOCK_UPTIME_RAW),drain_thread=now(CLOCK_THREAD_CPUTIME_ID);
        glFinish();
        drain_elapsed=now(CLOCK_UPTIME_RAW)-drain_wall;
        drain_cpu=now(CLOCK_THREAD_CPUTIME_ID)-drain_thread;
    }
    uint64_t elapsed=submission_elapsed,thread=submission_cpu;
    uint64_t (*measured_queries)(void)=dlsym(RTLD_DEFAULT,"eu4_frame_model_test_measured_queries");
    if(measured_queries && (!getenv("EU4_TEST_ABLATION") || strcmp(getenv("EU4_TEST_ABLATION"),"preparation")) && measured_queries()) return 13;
    unsigned char pixel[4];glReadPixels(16,16,1,1,GL_RGBA,GL_UNSIGNED_BYTE,pixel);
    GLenum error=glGetError();
    if(error!=GL_NO_ERROR || !pixel[3]) { fprintf(stderr,"validation: GL error=%u alpha=%u\n",error,pixel[3]);return 12; }
    printf("elapsed_ns=%llu cpu_ns=%llu draws=%u valid=1 pixel_alpha=%u preparation_wall_ns=%llu preparation_cpu_ns=%llu",
           (unsigned long long)elapsed,(unsigned long long)thread,frames*command_count,pixel[3],
           (unsigned long long)preparation_wall,(unsigned long long)preparation_cpu);
    if(report_completion) {
        printf(" submission_elapsed_ns=%llu submission_cpu_ns=%llu post_window_drain_elapsed_ns=%llu"
               " post_window_drain_cpu_ns=%llu submission_plus_drain_elapsed_ns=%llu submission_plus_drain_cpu_ns=%llu",
               (unsigned long long)submission_elapsed,(unsigned long long)submission_cpu,
               (unsigned long long)drain_elapsed,(unsigned long long)drain_cpu,
               (unsigned long long)(submission_elapsed+drain_elapsed),
               (unsigned long long)(submission_cpu+drain_cpu));
    }
    printf("\n");
    CGLSetCurrentContext(NULL);CGLDestroyContext(ctx);CGLDestroyPixelFormat(pf);
    free(vertices);free(indices);free(commands);return 0;
}

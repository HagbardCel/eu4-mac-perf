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

typedef void (*Active)(GLenum);
typedef void (*BindBuffer)(GLenum,GLuint);
typedef void (*Attrib)(GLuint);
typedef void (*AttribPointer)(GLuint,GLint,GLenum,GLboolean,GLsizei,const GLvoid *);
typedef void (*AttribDivisor)(GLuint,GLuint);
typedef void (*BindVAO)(GLuint);
typedef void (*GenVAO)(GLsizei,GLuint *);
typedef void (*UseProgram)(GLuint);
typedef void (*Uniform4)(GLint,GLsizei,const GLfloat *);
typedef void (*GenBuffer)(GLsizei,GLuint *);
typedef void (*BindFBO)(GLenum,GLuint);
typedef void (*GenFBO)(GLsizei,GLuint *);
typedef void (*AttachFBO)(GLenum,GLenum,GLenum,GLuint,GLint);
typedef GLenum (*CheckFBO)(GLenum);

static uint64_t now_ns(void) {
    struct timespec t; clock_gettime(CLOCK_UPTIME_RAW,&t);
    return (uint64_t)t.tv_sec*1000000000ull+t.tv_nsec;
}
static int set_mode(int m) {
    const char *path=getenv("EU4_CACHE_CONTROL");
    if (!path) return m==0;
    FILE *f=fopen(path,"r+b");
    if (!f) return 0;
    int ok=fputc(m,f)!=EOF;
    fclose(f);
    return ok;
}
static GLuint shader(GLenum type,const char *source) {
    GLuint id=glCreateShader(type);
    glShaderSource(id,1,&source,NULL);
    glCompileShader(id);
    GLint success=0; glGetShaderiv(id,GL_COMPILE_STATUS,&success);
    if (!success) return 0;
    return id;
}
int main(int argc,char **argv) {
    CGLPixelFormatAttribute attrs[]={kCGLPFAAccelerated,(CGLPixelFormatAttribute)0};
    CGLPixelFormatObj format=NULL; CGLContextObj context=NULL; GLint count=0;
    if (CGLChoosePixelFormat(attrs,&format,&count)!=kCGLNoError || !format) return 2;
    if (CGLCreateContext(format,NULL,&context)!=kCGLNoError || !context) return 3;
    if (CGLSetCurrentContext(context)!=kCGLNoError) return 4;
    GLuint texture=0; glGenTextures(1,&texture);
    glBindTexture(GL_TEXTURE_2D,texture);
    glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA8,16,16,0,GL_RGBA,GL_UNSIGNED_BYTE,NULL);
    GenFBO gen_fbo=(GenFBO)dlsym(RTLD_DEFAULT,"glGenFramebuffersEXT");
    BindFBO bind_fbo=(BindFBO)dlsym(RTLD_DEFAULT,"glBindFramebufferEXT");
    AttachFBO attach_fbo=(AttachFBO)dlsym(RTLD_DEFAULT,"glFramebufferTexture2DEXT");
    CheckFBO check_fbo=(CheckFBO)dlsym(RTLD_DEFAULT,"glCheckFramebufferStatusEXT");
    if (!gen_fbo || !bind_fbo || !attach_fbo || !check_fbo) return 5;
    GLuint fbo=0; gen_fbo(1,&fbo); bind_fbo(GL_FRAMEBUFFER_EXT,fbo);
    attach_fbo(GL_FRAMEBUFFER_EXT,GL_COLOR_ATTACHMENT0_EXT,GL_TEXTURE_2D,texture,0);
    if (check_fbo(GL_FRAMEBUFFER_EXT)!=GL_FRAMEBUFFER_COMPLETE_EXT) return 6;
    glViewport(0,0,16,16);
    if (argc>1 && !strcmp(argv[1],"--uniform-invalidation")) {
        UseProgram use_program=(UseProgram)dlsym(RTLD_DEFAULT,"glUseProgram");
        typedef void (*Uniform1i)(GLint,GLint);
        typedef void (*Uniform1iv)(GLint,GLsizei,const GLint *);
        Uniform1i uniform1i=(Uniform1i)dlsym(RTLD_DEFAULT,"glUniform1i");
        Uniform1iv uniform1iv=(Uniform1iv)dlsym(RTLD_DEFAULT,"glUniform1iv");
        Uniform4 uniform4=(Uniform4)dlsym(RTLD_DEFAULT,"glUniform4fvARB");
        if (!use_program || !uniform1i || !uniform1iv || !uniform4 || !set_mode(3)) return 24;
        GLuint vs=shader(GL_VERTEX_SHADER,"void main(){gl_Position=gl_Vertex;}");
        GLuint fs=shader(GL_FRAGMENT_SHADER,
                         "uniform int index; uniform vec4 color; "
                         "void main(){gl_FragColor=color*float(index);}");
        if (!vs || !fs) return 25;
        GLuint program=glCreateProgram(); glAttachShader(program,vs); glAttachShader(program,fs);
        glLinkProgram(program);
        GLint index=glGetUniformLocation(program,"index");
        GLint color=glGetUniformLocation(program,"color");
        if (index<0 || color<0) return 26;
        const GLfloat rgba[4]={0.25f,0.5f,0.75f,1.0f};
        CGLFlushDrawable(context); // Establish the rendering thread.
        use_program(program);
        for (int i=0;i<1000;i++) {
            uniform1i(index,2);
            uniform4(color,1,rgba);
        }
        const GLint changed=3;
        uniform1iv(index,1,&changed);
        uniform1i(index,2);
        GLint actual=0; glGetUniformiv(program,index,&actual);
        if (actual!=2) return 27;
        usleep(1100000); CGLFlushDrawable(context);
        puts("uniform cache survives unrelated setters and invalidates overwritten locations");
        return 0;
    }
    if (argc>1 && !strcmp(argv[1],"--stress")) {
        const GLfloat triangle[]={-1,-1, 1,-1, 0,1};
        const GLushort indices[]={0,1,2};
        GLuint vs=shader(GL_VERTEX_SHADER,"void main(){gl_Position=gl_Vertex;}");
        GLuint fs=shader(GL_FRAGMENT_SHADER,"void main(){gl_FragColor=vec4(0.25,0.5,0.75,1.0);}");
        if (!vs || !fs) return 21;
        GLuint program=glCreateProgram();
        glAttachShader(program,vs); glAttachShader(program,fs); glLinkProgram(program);
        GLint linked=0; glGetProgramiv(program,GL_LINK_STATUS,&linked);
        if (!linked) return 22;
        glUseProgram(program);
        glVertexPointer(2,GL_FLOAT,0,triangle);
        glEnableClientState(GL_VERTEX_ARRAY);
        uint64_t start=now_ns();
        for (int i=0;i<1000000;i++) {
            for (int j=0;j<20;j++) glBindTexture(GL_TEXTURE_2D,texture);
            glDrawElements(GL_TRIANGLES,3,GL_UNSIGNED_SHORT,indices);
            if (i%5700==5699) glFinish();
        }
        printf("stress_ns=%llu\n",(unsigned long long)(now_ns()-start));
        return 0;
    }
    if (argc>1 && !strcmp(argv[1],"--contexts")) {
        Active first_active=(Active)dlsym(RTLD_DEFAULT,"glActiveTextureARB");
        if (!first_active || !set_mode(1)) return 15;
        CGLFlushDrawable(context); // Establish the rendering thread.
        first_active(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D,texture);
        glBindTexture(GL_TEXTURE_2D,texture);
        glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_NEAREST);
        CGLContextObj other=NULL;
        if (CGLCreateContext(format,context,&other)!=kCGLNoError || !other) return 16;
        if (CGLSetCurrentContext(other)!=kCGLNoError) return 17;
        first_active(GL_TEXTURE0);
        for (int i=0;i<200;i++) glBindTexture(GL_TEXTURE_2D,texture);
        glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_LINEAR);
        CGLFlushDrawable(other); usleep(1100000); CGLFlushDrawable(other);
        if (CGLSetCurrentContext(context)!=kCGLNoError) return 18;
        first_active(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D,texture);
        glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_NEAREST);
        GLint filter=0; glGetTexParameteriv(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,&filter);
        if (filter!=GL_NEAREST) return 19;
        CGLFlushDrawable(context); usleep(1100000); CGLFlushDrawable(context);
        puts("context changes retained correct shared texture state");
        return 0;
    }
    Active active=(Active)dlsym(RTLD_DEFAULT,"glActiveTextureARB");
    BindBuffer bind_buffer=(BindBuffer)dlsym(RTLD_DEFAULT,"glBindBufferARB");
    Attrib enable=(Attrib)dlsym(RTLD_DEFAULT,"glEnableVertexAttribArrayARB");
    Attrib disable=(Attrib)dlsym(RTLD_DEFAULT,"glDisableVertexAttribArrayARB");
    AttribPointer pointer=(AttribPointer)dlsym(RTLD_DEFAULT,"glVertexAttribPointerARB");
    AttribDivisor divisor=(AttribDivisor)dlsym(RTLD_DEFAULT,"glVertexAttribDivisorARB");
    BindVAO bind_vao=(BindVAO)dlsym(RTLD_DEFAULT,"glBindVertexArrayAPPLE");
    GenVAO gen_vao=(GenVAO)dlsym(RTLD_DEFAULT,"glGenVertexArraysAPPLE");
    GenBuffer gen_buffer=(GenBuffer)dlsym(RTLD_DEFAULT,"glGenBuffersARB");
    UseProgram use_program=(UseProgram)dlsym(RTLD_DEFAULT,"glUseProgram");
    Uniform4 uniform=(Uniform4)dlsym(RTLD_DEFAULT,"glUniform4fvARB");
    if (!active || !bind_buffer || !enable || !disable || !pointer || !divisor ||
        !bind_vao || !gen_vao || !gen_buffer || !use_program || !uniform) return 7;
    GLuint vao=0,buffer=0; gen_vao(1,&vao); bind_vao(vao); gen_buffer(1,&buffer);
    bind_buffer(GL_ARRAY_BUFFER,buffer);
    const GLfloat vertices[]={-1,-1,0,1, 1,-1,0,1, 0,1,0,1};
    glBufferDataARB(GL_ARRAY_BUFFER,sizeof(vertices),vertices,GL_STATIC_DRAW);
    GLuint vs=shader(GL_VERTEX_SHADER,"void main(){gl_Position=gl_Vertex;}");
    GLuint fs=shader(GL_FRAGMENT_SHADER,"uniform vec4 color; void main(){gl_FragColor=color;}");
    if (!vs || !fs) return 8;
    GLuint program=glCreateProgram(); glAttachShader(program,vs); glAttachShader(program,fs);
    glLinkProgram(program); GLint linked=0; glGetProgramiv(program,GL_LINK_STATUS,&linked);
    if (!linked) return 9;
    GLint location=glGetUniformLocation(program,"color");
    if (location<0) return 10;
    const GLfloat color[4]={0.25f,0.5f,0.75f,1.0f};
    unsigned char reference[16*16*4];
    GLenum reference_error=GL_NO_ERROR;
    const int modes[]={0,1,2,3,0,1};
    for (unsigned phase=0;phase<6;phase++) {
        int m=modes[phase]; if (!set_mode(m)) return 11;
        uint64_t end=now_ns()+2200000000ull;
        while (now_ns()<end) {
            active(GL_TEXTURE0); active(GL_TEXTURE0);
            glBindTexture(GL_TEXTURE_2D,texture); glBindTexture(GL_TEXTURE_2D,texture);
            glTexEnvf(GL_TEXTURE_ENV,GL_TEXTURE_ENV_MODE,(GLfloat)GL_MODULATE);
            glTexEnvf(GL_TEXTURE_ENV,GL_TEXTURE_ENV_MODE,(GLfloat)GL_MODULATE);
            glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_NEAREST);
            glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_NEAREST);
            bind_buffer(GL_ARRAY_BUFFER,buffer); bind_buffer(GL_ARRAY_BUFFER,buffer);
            pointer(0,4,GL_FLOAT,GL_FALSE,0,0); pointer(0,4,GL_FLOAT,GL_FALSE,0,0);
            divisor(0,0); divisor(0,0);
            enable(0); enable(0); disable(0); disable(0);
            use_program(program); use_program(program);
            uniform(location,1,color); uniform(location,1,color);
            glClearColor(0,0,0,1); glClear(GL_COLOR_BUFFER_BIT);
            glBegin(GL_TRIANGLES);
            glVertex2f(-1,-1); glVertex2f(1,-1); glVertex2f(0,1);
            glEnd();
            unsigned char pixels[sizeof(reference)];
            glReadPixels(0,0,16,16,GL_RGBA,GL_UNSIGNED_BYTE,pixels);
            if (phase==0) memcpy(reference,pixels,sizeof(reference));
            else if (memcmp(reference,pixels,sizeof(reference))) return 12;
            CGLFlushDrawable(context);
            usleep(10000);
        }
        GLenum error=glGetError();
        if (phase==0) reference_error=error;
        else if (error!=reference_error) return 23;
    }
    GLfloat got[4]={0}; glGetUniformfv(program,location,got);
    if (memcmp(got,color,sizeof(color))) return 13;
    GLint filter=0; glGetTexParameteriv(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,&filter);
    if (filter!=GL_NEAREST) return 14;
    const GLfloat linear=(GLfloat)GL_LINEAR;
    glTexParameterfv(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,&linear);
    glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_NEAREST);
    glGetTexParameteriv(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,&filter);
    if (filter!=GL_NEAREST) return 18;
    glLinkProgram(program); location=glGetUniformLocation(program,"color");
    if (location<0) return 19;
    use_program(program);
    uniform(location,1,color); glGetUniformfv(program,location,got);
    if (memcmp(got,color,sizeof(color))) return 20;
    puts("state and pixels match in every mode");
    return 0;
}

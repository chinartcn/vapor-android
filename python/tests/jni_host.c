/*
 * jni_host.c —— 本机验证宿主：用 JNI Invocation API 启动 JVM，返回 JavaVM*。
 * Python 测试脚本拿到 JavaVM* 后注入 libvapor，即可验证 JNI 桥接。
 *
 * 编译:
 *   gcc -shared -fPIC -I$JAVA_HOME/include -I$JAVA_HOME/include/linux \
 *       python/tests/jni_host.c -o python/tests/libjni_host.so \
 *       -L$JAVA_HOME/lib/server -ljvm -Wl,-rpath,$JAVA_HOME/lib/server
 */
#include <jni.h>
#include <stdio.h>

static JavaVM *g_vm = NULL;
static JNIEnv *g_env = NULL;

/* 启动 JVM，返回 JavaVM*（NULL 表示失败）。可重复调用，幂等。 */
void *host_start_jvm(void) {
    if (g_vm) return g_vm;
    JavaVMOption options[1];
    options[0].optionString = "-Djava.class.path=.";
    JavaVMInitArgs vm_args;
    vm_args.version = JNI_VERSION_1_6;
    vm_args.nOptions = 1;
    vm_args.options = options;
    vm_args.ignoreUnrecognized = JNI_FALSE;
    jint rc = JNI_CreateJavaVM(&g_vm, (void **)&g_env, &vm_args);
    if (rc != JNI_OK) {
        fprintf(stderr, "JNI_CreateJavaVM failed rc=%d\n", (int)rc);
        g_vm = NULL;
        return NULL;
    }
    return g_vm;
}

/* 销毁 JVM（进程退出时由 Python 调用，可省略） */
void host_destroy_jvm(void) {
    if (g_vm) {
        (*g_vm)->DestroyJavaVM(g_vm);
        g_vm = NULL;
        g_env = NULL;
    }
}

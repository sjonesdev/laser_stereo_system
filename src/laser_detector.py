from functools import wraps
import math
import os
import time
import cupy
import numpy as np
from numba import cuda, prange
import numba as nb
from PIL import Image

# Constants, should move these to param yaml file if gonna use with ROS
WINLEN = 5 # works for 1080p and 2.2k for Zed mini
GVAL_MAX_VAL = -2000.

def timeit(func):
    @wraps(func)
    def timeit_wrapper(*args, **kwargs):
        start_time = time.perf_counter()
        result = func(*args, **kwargs)
        end_time = time.perf_counter()
        total_time = end_time - start_time
        print(f'Function {func.__name__}{args} {kwargs} Took {total_time:.4f} seconds')
        return result
    return timeit_wrapper

@timeit
def generate_laser_reward_image(img, color_weights):
    # type:(cp.ndarray, tuple[float,float,float]) -> cp.ndarray
    '''Creates a greyscale image where each pixel's value represents the likely of the pixel being part of a laser.

    :param img: (cv.Mat) MxNx3 RGB image, assumes Red-Green-Blue (RGB) color order
    :param color_weights: (tuple[float,float,float]) Values to weight colors of 
    pixels to detect laser lines, in the order (R,G,B). In the conversion to greyscale, 
    determines how much of a color's value is added to the greyscale value. e.g. if 
    (R,G,B)=(0.1, 0.5, 0.2), for a pixel with RGB(128,255,50), the resulting greyscale 
    value would be 0.1 * 128 + 0.5 * 255 + 0.2 * 50 = 150.3 ≈ 150. These values are 
    usually obtained by cross-referencing the color response charts of your camera to 
    the wavelength of your laser light.

    :return: MxN greyscale cv.Mat image or otherwise compatible arraylike
    '''
    # maybe force OpenCV to use integer images instead of floats to save memory?
    return img * color_weights
    # return img[:,:,0] * color_weights[0] + img[:,:,1] * color_weights[1] + img[:,:,2] * color_weights[2]


@cuda.jit
def gpu_gvals(img, out):
    winstartrow, winstartcol = cuda.grid(2)
    # if(winstartrow % 64 == 0 and winstartcol % 64 == 0): print(winstartrow, winstartcol)
    if(winstartrow + WINLEN < img.shape[0] and winstartcol < img.shape[1]):
        G = 0
        for row in range(winstartrow, winstartrow+WINLEN):
            G += (1 - 2*abs(winstartrow - row + (WINLEN - 1) / 2)) * img[row, winstartcol] #idk if this last part is right
        out[winstartrow,winstartcol] = G

# @timeit
# @nb.jit
# def cpu_mt_gvals(img):
#     rows = img.shape[0]
#     cols = img.shape[1]
#     gvals = []
#     for col in prange(cols):
#         for winstart in prange(rows-WINLEN):
#             G = 0
#             for row in prange(winstart, winstart+WINLEN):
#                 G += (1 - 2*abs(winstart - row + (WINLEN - 1) / 2)) * img[row,col] #idk if this last part is right
#             gvals.append((col, winstart+WINLEN//2, G))
#     return gvals

@timeit
def cpu_gvals(img):
    rows = img.shape[0]
    cols = img.shape[1]
    gvals = []
    for col in range(cols):
        for winstart in range(rows-WINLEN):
            G = 0
            for row in range(winstart, winstart+WINLEN):
                G += (1 - 2*abs(winstart - row + (WINLEN - 1) / 2)) * img[row,col] #idk if this last part is right
            gvals.append((col, winstart+WINLEN//2, G))
    return gvals

def generate_candidate_laser_pt_img(img):
    '''Takes a greyscale laser reward image and thresholds it to a binary image

    :param img: MxN greyscale cv.Mat image or otherwise compatible arraylike with dtype=np.float32

    :return: MxN binary cv.Mat image or otherwise compatible arraylike
    ''' 

    # @cuda.jit
    # def integrate(colvec, out):
    #     idx = cuda.grid(1)
    #     out[idx] = np.sum(colvec[idx:idx+WINLEN])

    # GPU for col loop and row loop

    # GPU for col loop with memoization

    # GPU for row loop
    # gvals = cp.zeros(img.shape)
    # we want a thread per element
    # block/thread strategy [WIP]
    # - start with one block, do up to 256 threads, 
    # - then increment num blocks until fill both with 256 threads 
    # - and so on until we reach 16 blocks, 
    # - at which point we start incrementing thread count up to 1024
    # - this gives us up to 16,384 threads
    # - 2.2k mono image has 2,742,336 elements/pixels
    # - make each thread do multiple?

    # POT improvements 
    # - idk
    # - set minimum number of elements below which CPU will be used for less overhead
    # nbr_thread_per_block = 256 # modern nvidia GPUs have 1024 threads per block max, often standard of 256 is used for convenience
    # nbr_block_per_grid = 2 # jetson nano has 32 blocks max
        
    # for col in range(cols):
    #     d_col = cuda.to_device(img[:,col])
    #     d_out = cuda.device_array(rows)
    #     integrate[nbr_block_per_grid, nbr_thread_per_block](d_col, d_out)
        # need to compare with memoization
    threadsperblock = (32,32) # thread dims multiplied must not exceed max threads per block
    blockspergrid_x = int(math.ceil(img.shape[0] / threadsperblock[0]))
    blockspergrid_y = int(math.ceil(img.shape[1] / threadsperblock[1]))
    blockspergrid = (blockspergrid_x, blockspergrid_y)
    print(f"\nBlockDimX = {blockspergrid[0]} \nBlockDimY = {blockspergrid[1]} \nthreadsPerBlock = {threadsperblock}\n")
    print(f"Image shape: {img.shape}\n")

    start_time = time.perf_counter()
    d_img = cuda.to_device(img)
    output_global_mem = cuda.device_array(img.shape)
    gpu_gvals[blockspergrid, threadsperblock](d_img, output_global_mem)
    output = output_global_mem.copy_to_host()
    end_time = time.perf_counter()
    total_time = end_time - start_time
    print(output)
    print(f'\nGPU gval gen took {total_time:.4f} seconds')

    # start_time = time.perf_counter()
    # cpu_gvals(img)
    # end_time = time.perf_counter()
    # total_time = end_time - start_time
    # print(f'CPU gval gen took {total_time:.4f} seconds')    

    # threshold
    # can use this to help choose threshold
    # expectedgoodgvals = int(img.shape[0] * 15 * 1.6)#1.4) # room for plenty of outliers
    # gvals = np.sort(output.flatten())
    # print(f"Gvals Stats \n\tmean: {np.average(gvals)} \n\tmedian: {np.median(gvals)} \n\tmin: {np.min(gvals)} \n\tmax:{np.max(gvals)}")
    # goodgvals = gvals[:expectedgoodgvals]
    # print(f"Good Gvals Stats \n\tmean: {np.average(goodgvals)} \n\tmedian: {np.median(goodgvals)} \n\tmin: {np.min(goodgvals)} \n\tmax:{np.max(goodgvals)}")
    output = output < GVAL_MAX_VAL * output #use cp array here?


def imagept_laserplane_assoc(img, planes):
    # type:(cp.ndarray, list[tuple(float, float, float)]) -> list[list[tuple(float,float,float)]]
    '''
    Associates each laser points in the image with one of the provided laser planes or throws it out.

    :param img: MxN binary cv.Mat image or otherwise compatible arraylike
    :param planes: (list[tuple(float,float,float)]) Planes of light projecting from a laser equiped with a diffractive optical element (DOE) described by their normal vectors in the camera reference frame.

    :return: (list[list[tuple(float,float,float)]]) Image points organized plane, with the index of the plane in the planes array corresponding to the index of its member points in the returned array.
    '''
    pass

def extract_laser_points(img):
    # type:(cp.ndarray) -> list[tuple(float,float,float)]
    '''
    Finds 3D coordinates of laser points in an image

    img - OpenCV Mat or otherwise compatible arraylike
    '''
    pass


if __name__ == "__main__":

    gpu = cuda.get_current_device()
    print(gpu)
    print(type(gpu))
    print("name = %s" % gpu.name)
    print("maxThreadsPerBlock = %s" % str(gpu.MAX_THREADS_PER_BLOCK))
    print("maxBlockDimX = %s" % str(gpu.MAX_BLOCK_DIM_X))
    print("maxBlockDimY = %s" % str(gpu.MAX_BLOCK_DIM_Y))
    print("maxBlockDimZ = %s" % str(gpu.MAX_BLOCK_DIM_Z))
    print("maxGridDimX = %s" % str(gpu.MAX_GRID_DIM_X))
    print("maxGridDimY = %s" % str(gpu.MAX_GRID_DIM_Y))
    print("maxGridDimZ = %s" % str(gpu.MAX_GRID_DIM_Z))
    print("maxSharedMemoryPerBlock = %s" % str(gpu.MAX_SHARED_MEMORY_PER_BLOCK))
    print("asyncEngineCount = %s" % str(gpu.ASYNC_ENGINE_COUNT))
    print("canMapHostMemory = %s" % str(gpu.CAN_MAP_HOST_MEMORY))
    print("multiProcessorCount = %s" % str(gpu.MULTIPROCESSOR_COUNT))
    print("warpSize = %s" % str(gpu.WARP_SIZE))
    print("unifiedAddressing = %s" % str(gpu.UNIFIED_ADDRESSING))
    print("pciBusID = %s" % str(gpu.PCI_BUS_ID))
    print("pciDeviceID = %s" % str(gpu.PCI_DEVICE_ID))

    # example
    # x = cp.arange(10, dtype=cp.float32).reshape(2,5)
    # y = cp.arange(5, dtype=cp.float32)
    # parallel_sum = cp.ElementwiseKernel(
    #     'float32 x, float32 y',
    #     'float32 z',
    #     'z = x + y',
    #     'parallel_sum'
    # )
    # z = cp.empty((2,5), dtype=cp.float32)
    # parallel_sum(x,y,z)


    # gvals generation comparison
    img_folder = "calib_imgs"
    imgs = []
    cpimgs = []
    filenames = []
    for filename in os.listdir(img_folder):
        img = Image.open(os.path.join(img_folder, filename))
        if img is not None:
            imgs.append(np.asarray(img))
            cpimgs.append(cupy.asarray(img))
            filenames.append(filename)
    print("found images: %s\n" % filenames)

    numpyrewardtimes = 0
    for img in imgs:
        start_time = time.perf_counter()
        color_weights = (0.08, 0.85, 0.2)
        reward = np.sum(img * color_weights, axis=2)
        end_time = time.perf_counter()
        total_time = end_time - start_time
        numpyrewardtimes += total_time
        print(f'\nNumpy reward img took {total_time:.4f} seconds')

        gvals = generate_candidate_laser_pt_img(reward)
    
    cupyrewardtimes = 0
    for img in imgs:
        start_time = time.perf_counter()
        color_weights = (0.08, 0.85, 0.2)
        reward = cupy.sum(img * color_weights, axis=2)
        end_time = time.perf_counter()
        total_time = end_time - start_time
        cupyrewardtimes += total_time
        print(f'Cupy reward img took {total_time:.4f} seconds')

        gvals = generate_candidate_laser_pt_img(reward)
    
    print(f'\nNumpy reward img avg = {numpyrewardtimes/len(imgs):.4f} seconds')
    print(f'\nCupy reward img avg = {cupyrewardtimes/len(imgs):.4f} seconds')

    


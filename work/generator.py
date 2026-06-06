from random import randint as r, shuffle as sf


def generator():
    print(r(1, 10))

'''
1. generate random int array with sizeof N in list Ns
Ns = [100, 50, 10]

def generator():
    N = Ns.pop()
    print(N)
    print(*[r(1,10) for _ in range(N)])
'''

'''
# 2. Tree data generation
def generator():
    N = r(1, 10**5)
    print(N)

    for i in range(1, N):
        print(i, r(i + 1, N))
'''

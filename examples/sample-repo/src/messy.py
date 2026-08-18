def tangled(a, b, c, d):
    if a:
        if b:
            if c:
                if d:
                    return 1
                else:
                    return 2
            elif d:
                return 3
        elif c:
            return 4
    elif b:
        return 5
    return 0

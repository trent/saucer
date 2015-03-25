from setuptools import setup


setup(
    name='saucer',
    version='0.01',
    url='https://github.com/trent/saucer',
    license='BSD',
    author='Trent Jurewicz',
    author_email='tjurewicz@gmail.com',
    description='A packaging helper for Python web apps. Based on and extends Armin Ronacher\'s Platter at https://github.com/mitsuhiko/platter.',
    long_description=__doc__,
    py_modules=['saucer'],
    platforms='any',
    dependency_links = ['https://github.com/mitsuhiko/platter/tarball/master#egg=platter-1.0-dev'],
    install_requires=[
        'click>=2.0',
        'platter>=1.0-dev',
    ],
    entry_points='''
        [console_scripts]
        saucer=saucer:cli
    '''
)

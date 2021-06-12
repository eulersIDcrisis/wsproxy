from setuptools import setup, find_packages

setup(
    name='wsproxy',
    version='0.1.0',
    description="Set of Websocket tools for tunneling and monitoring",
    author="eulersIDcrisis",
    packages=find_packages(include=['wsproxy', 'wsproxy.*']),
    install_requires=[
        # Require 'tornado', minimum version of 6.0.1
        # Could possibly waive this to tornado 5.X, not sure.
        'tornado>=6.0.1',
        'psutil>=5.8.0'
    ],
    setup_requires=['flake8'],
    entry_points={
        'console_scripts': [
            'wsproxy=wsproxy.main:main'
        ]
    }
)
